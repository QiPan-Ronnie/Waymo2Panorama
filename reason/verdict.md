# Verdict: DB-23 next experiment choice

Rounds completed: 2
Convergence: yes

## Winning Argument

ID: ARG-A83F (revealed: Rejudge/complete DiT360 out-of-FOV ground/full outpaint first)

Score: 9/10

Summary: The next experiment should close the unfinished DiT360 out-of-FOV branch, not reopen near-ground seam repair. First fetch/re-gate/vision-judge the already-run ground/full outputs from `results/dit360_outpaint_v2`; only if outputs are missing or stale should a strict one-mask/one-seed bottom-band run be launched on A100.

Key evidence:
- DB-19 sky-only outpaint is the one consistently positive DiT360 result, so out-of-FOV completion is a valid constrained use of generation.
- DB-14/21/22 collectively reject near-ground seam-line DiT repair: old mask misalignment, current aligned masks, and rectilinear diagnosis all point to semantic ground redraw.
- D4b ground/full outpaint was recorded as run but not judged after a tunnel outage, so the ledger has a concrete unresolved item.
- The user explicitly asked to complete DiT360 content and keep A100 useful.

## Minority Positions

- ARG-F21C: Strong strategic direction, but should be a separate follow-up brief: raw-camera evidence pack / reference-guided seam guidance. It is more promising than more blind DiT seam knobs, but it does not need to block DB-23.
- ARG-C64D: Lost because DB-21/22 weakened the premise that yaw/RF knobs can turn semantic redraw into faithful geometry repair.
- ARG-9B7E: Useful for reporting, but existing docs already capture the commercial-method lesson. It is not the best immediate A100 action.

## Synthesis

DB-23 must define success narrowly: object-free, visually plausible out-of-FOV bottom/outer-band completion. It must not claim to straighten the right-ground white-line seam or fix curb geometry. Any generated car/person/sign, new lane/curb structure, or visible rewrite of captured road/building content kills the result.

After DB-23, the next non-local-optimum direction should be an AV evidence-pack brief: project raw camera evidence, LiDAR/epipolar validity, and ERP/cube seam panels into one diagnostic package. That aligns with the DiT360 and CubeComposer lessons without using blind generation as a stitching solver.

## Confidence

High for immediate ordering. Medium on whether ground/full outpaint will pass; the experiment is designed to close it quickly if it fails.
