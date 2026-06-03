# Verdict: next exploration after DB-14 old-r008 rejection

Rounds completed: 2
Convergence: yes

## Winning Argument

ID: ARG-7C2F (revealed: DB-21 current-base-aligned thin seam mask first)
Score: 9/10

Summary: The next action should be DB-21: build a current-base-aligned thin mask, visually approve its overlay, then run only a tiny G_bmw tau{5,8} DiT360 test. This directly tests the DB-14 failure mechanism without committing to another broad DiT sweep.

Key evidence:
- DB-14's `trimap_preview.jpg` shows the old r008 core is historical fixed vertical strips, not the current residual seam.
- Google Street View uses optical flow only where correspondences are reliable, then subtle global spline warping; this supports "small, inspected, confidence-gated edits" rather than broad generated repair.
- Meta Surround360 similarly uses optical-flow novel-view synthesis and compositing, but acknowledges occlusion/flow ambiguity; this supports a narrow kill-gated test, not reopening the whole flow route.
- CubeComposer is relevant mainly as a cube-face continuity idea, not as a direct AV stitching solution. Borrow cube-aware overlay/precheck ideas; do not run the full CubeComposer model first.

## Minority Positions

- ARG-91BD: Lost because CubeComposer's full model targets perspective-video-to-360 generation. Its cube-aware padding/blending is useful as a representation hint, but a full pivot is too expensive before the cheap DB-21 falsifier.
- ARG-44A9: Lost because local evidence already shows optical-flow/geometry floors in textureless/occluded BMW seams. Commercial methods inform constraints but do not guarantee our rig can match them.
- ARG-E603: Lost as the immediate next step because it stops discovery too early. It remains the fallback deliverable path if DB-21 fails.

## Synthesis

DB-14 should be read precisely: blind old-mask reuse is rejected. The failure does not close DiT360 seam repair, because the mask did not cover the current defect correctly and in BEST actively cut salient objects.

The commercial systems point in the same direction: seam repair must be subtle, confidence-gated, and geometry-aware. Google/Meta are not doing unconstrained image invention. Therefore DB-21 must require an overlay review before GPU and must be killed on any object cut, lane/curb bend, or no-op.

CubeComposer should be inspected as a source of cube-face context and boundary-continuity ideas. It should not become a full model-run direction unless DB-21 shows ERP-space mask geometry itself is the blocker.

## Confidence

High for the next-step ordering. Medium on whether DB-21 will produce a visually better image; it is explicitly a cheap falsification, not a promised win.
