# DB-226 fixed monotonic luminance-response design

Date: 2026-07-31
Status: approved by the user; diagnostic-first implementation may begin
Decision brief: `agent/decision_briefs.md`, DB-226

## 1. Problem and evidence boundary

The production renderer currently solves one luminance exposure offset per camera and frame from same-3D-point observations. DB-221 fixed the disconnected-graph failure mode. DB-215 then showed that the remaining signed spatial residual pattern is repeatable within a log but does not transfer across logs, so a fixed 2D per-camera correction field is not justified.

One narrower hypothesis remains: a camera may have a fixed nonlinear response. A scalar aligns one brightness level only; a fixed response mismatch can leave dark, mid-tone, and highlight residuals with opposite signs. The experiment must distinguish that response from scene structure, parallax, occlusion, view-dependent reflectance, exposure changes, and saturation.

This is a calibration hypothesis, not an appearance objective. The route passes only with held-out-by-log evidence and full-resolution vision checks. A visually smoother output without transferable same-ray evidence is a failure.

## 2. Chosen approach and rejected alternatives

The chosen approach is a per-camera, one-dimensional, strictly monotonic log-luminance response map learned across training logs and frozen for held-out logs. It has no image coordinates, scene/frame identity, or per-channel degrees of freedom.

Rejected for this route:

- Fixed or learned 2D camera-coordinate fields: killed by DB-215 cross-log evidence.
- Feathering or multiband blending: mixes different 3D surfaces near parallax boundaries and can recreate ghosts/text tearing.
- Per-channel AWB curves: changes hue and reopens the DB-208 failure class.
- Per-frame tone curves: can always fit a scene but cannot establish a camera property.
- Generated or learned seam completion: fabricates pixels and cannot answer the photometric root-cause question.

## 3. Measurement contract

For every curved ownership boundary camera pair and same-3D ray, the diagnostic records sufficient statistics derived from:

- raw `logY_a`, `logY_b`, where `Y = mean(R,G,B)` matches the existing gain solver;
- corrected `logY_a`, `logY_b` after the existing scalar gains;
- signed residual `corrected_logY_b - corrected_logY_a`;
- a shared corrected-brightness coordinate `0.5 * (corrected_logY_a + corrected_logY_b)`;
- sample count, saturation count/fraction, pair `rho_log_luma`, and current gains;
- the existing same-ray validity and poison rejection, plus parallax-angle summaries when available.

Brightness profiles use fixed absolute log-luminance edges shared by every frame and log, not per-frame quantiles. Empty or under-supported bins remain explicit nulls. The report includes median signed residual, MAD, absolute p90, and counts per bin. It also keeps the existing camera-coordinate grids so brightness and spatial explanations can be compared rather than conflated.

The first code slice only adds these sufficient statistics. It does not alter rendered pixels.

## 4. Split and falsification analysis

The run is scene-stratified and split by whole log before looking at candidate output. No frame from a held-out log may influence response estimation or knot selection.

The diagnostic asks four pre-candidate questions:

1. Coverage: do camera pairs have at least three supported, non-saturated fixed brightness bins over a useful range?
2. Repeatability: after removing each frame's constant residual offset, does the residual-vs-brightness direction recur across training logs?
3. Transfer: does the training profile predict the held-out signed profile better than the scalar-only zero-shape baseline?
4. Geometry sensitivity: does the conclusion remain under predeclared correlation sensitivity bands and after excluding high-parallax samples?

If the answer is no, the route is killed and no pixel candidate is built. Low coverage is “unknown”, not a pass or a failure.

## 5. Candidate model, only after diagnostic pass

Let `z = log(Y)` and let `h_c(z)` be camera `c`'s fixed piecewise-linear correction. Fixed raw-luminance knots are shared across cameras. Slopes are parameterized positive, so `z + h_c(z)` is strictly increasing. The common unidentifiable gauge is fixed by an identity reference/zero-mean constraint. Training jointly uses same-ray pair residuals from training logs and per-frame camera exposure offsets; robust loss and evidence weights prevent one bad pair from dominating.

At application time:

1. Compute raw luminance `Y`.
2. Compute `Y' = exp(log(Y) + h_c(log(Y)))`.
3. Scale all RGB channels by the same `Y'/Y` factor.
4. Re-run the existing scalar gain solver on the transformed samples.

This preserves chromatic ratios for non-clipped pixels. The transform is gated off by default until all acceptance checks pass.

## 6. Acceptance and kill rules

The candidate passes only if one frozen model:

- improves the majority of held-out curved-boundary pairs and the majority of held-out logs versus scalar-only;
- improves robust signed-step and absolute-residual metrics without concentrating the gain in low-correlation/high-parallax samples;
- preserves `R/G` and `B/G` numerically for non-clipped pixels;
- introduces no additional clipping band;
- passes full-resolution vision checks on territory blocks, people, vehicles, and text in at least three unseen logs.

Any hue shift, ghost, text tear, real-shadow erasure, scene-specific refit requirement, or non-transferable held-out profile kills the route. No parameter sweep may be used to rescue it after the registered failure.

## 7. Implementation sequence

1. Add tests for fixed brightness bins, signed medians, unsupported bins, gain-shift behavior, and chroma invariance.
2. Add diagnostic statistics and wire them into the gated JSON report.
3. Run a multi-log A100 diagnostic with multiple workers and archive the exact split and summaries.
4. Analyze train/held-out repeatability and geometry sensitivity.
5. Only on pass, add tests and a gated monotonic map implementation, run held-out numerical and visual A/B, and then decide whether production promotion is justified.

## 8. Safety and provenance

Executor credentials remain outside the repository. Remote job success requires a completed job record, exit code zero, expected manifests, and downloaded evidence. Existing untracked deliverables are preserved. Production defaults and the delivered 555 samples are untouched during the diagnostic.
