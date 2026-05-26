# Letterbox-as-ego-mask "fix" — NEG record

**Date**: 2026-05-26
**Status**: Rejected (not shipped). Visual evidence PNGs deleted.

## Hypothesis tested

Pi3-cache images (`outputs/phase3/p3.1_multi_anchor/anchor_XXX/image_*.png`) are
504×504 with ~24% black letterbox padding. The hypothesis: that padding leaks
into L1 sphere projection's blend at overlap regions, producing the visible
"white halo / washed-out" trace user flagged in the 5.22 prompt.

Mechanical "fix": build per-cam binary mask from the image itself
(`(image.sum(axis=-1) > 10).astype(np.uint8)`) and pass to
`render_camera_to_erp(..., ego_mask=...)`.

## Why it was claimed to work

Anchor 60 local CPU diff metrics:
- 9.71% pixels changed by >5 levels (concentrated in overlap regions)
- max diff 151 levels
- ERP black fraction 0.72 → 0.70 (more coverage)

I read these numbers as "fix worked" without visually verifying the result.

## Why it doesn't work

User pulled the with-mask PNG up, drew a red box around the same overlap
region where the white halo had been, and showed it had been **replaced by a
black gap, not removed**.

Mechanism: masking out letterbox padding causes the cam projection to skip
those ERP pixels. If the neighbor cam doesn't actually cover that ERP
position (which it doesn't, in the parallax-displaced region), you get a
black hole instead of a white halo. Neither is correct content; both are
artifacts. Mask trades one artifact for another at the same location.

## Root cause (re-confirmed)

The white halo is parallax. Sphere projection's infinity-depth assumption
projects near-field objects to different ERP positions per cam. Multi-band
blend then mixes inconsistent content in the overlap, producing the wash.
This is a structural property of L1; no mask or input filter can fix it. The
fix has to act on the geometry (depth-aware warping = WS4 A2 sparse
displacement, or seam routing through low-disparity pixels = WS4 B1
graphcut).

Additionally: `l1_erp.png` from the 5.21 handoff (which seemed clean) **also
has the halo** when you look carefully. The "clean" appearance was a
combination of darker overall tone, heavier black borders, and smaller
display size disguising the artifact. The halo affects every route,
including the apparent reference.

## What was actually shipped vs not

- **Shipped**: nothing. No code in `code/` was changed by this experiment.
- **Reverted**: 4 PNGs in `deliverables/letterbox_fix_visual/` (commit
  044cde4) were deleted in a follow-up commit.
- **Kept**: this note, so the failure mode is documented for future agents.

## Lessons

1. Diff metrics (% pixels changed, black fraction, max-diff) describe
   **whether output changed**, not whether the change was good. A fix can
   move pixels in a way that "looks different" without being correct.
   Always pair diff numbers with a visual inspection of the same region.
2. When the user has already shown a specific artifact (red box on a specific
   region), the verification protocol must compare that exact region pre/
   post fix, not aggregate the whole frame.
3. "Reference output looks clean" can mean "artifact is less visible due to
   tone/contrast/scale," not "artifact is absent." Always sanity-check the
   reference using vision before treating it as ground truth.
