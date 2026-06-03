# Codex (gpt-5.5 xhigh) adversarial verdict #2 — 2026-06-02 (the "is the wall final?" check)

Clean extract of codex's final answer (full transcript: `2026-06-02-codex-adversarial-02.log`, prompt `_prompt_02.md`). Images shown: loosened plane-sweep (smear), evidence-pack viz, DFWD⊕L1 gate.

## Blunt verdict
"You are NOT quitting early for the shippable path. You HAVE hit a real wall for: **same-frame, source-faithful, non-generative, true ego-center seam repair without reliable dense depth.** But TIGHTEN the claim: don't say 'dense depth is unrecoverable' — say **your current depth sources don't recover it cleanly enough.** Your `_planesweep.py` is a reasonable kill-test but NOT a full MVS system (24 planes, raw-RGB agreement, 5×5 blur, no Census/ZNCC, no SGM/MRF, no subpixel, no temporal/multi-view regularization)."
- The strongest evidence = the LiDAR copy test (straddle=0, best copy ~16–21px from true) → kills copy-selection / graphcut / hard-select / "pick the better ghost."

## Attack on my candidate escapes
- **Global spline/mesh warp: NOT worth a major build.** Google uses it for *subtle overlap repair under weak constraints*, not true single-center; with "true lies beyond both copies," it has no target to warp toward → aligns one wrong copy to another wrong copy.
- **Layered LDI / two-layer: theoretically the right class, NOT killed** — but concretely it reduces to "get accurate dense LAYERED depth"; without that it's just smarter ghosting.
- **Trained model: no-GT is NOT a hard blocker.** Train via leave-one-camera-out / cross-camera reprojection + fixed calibration + sparse LiDAR + occlusion masks + smoothness. MVSNet (cost-volume via differentiable homography) / MPI (learned layered) are the precedent. Blocker = engineering risk / occlusion-dynamics / domain reliability, NOT supervision existence.
- **★ RETARGETING (the big reframe): a true ego-center ERP is a PUNISHING target for a non-central 7-cam rig. `L1/align` is a VALID multi-perspective source-faithful panorama target. Jump/ODS are NOT "one ego-center" — they use view interpolation + compositing; even Google later added MVS for complex layers/occlusion.** (i.e. Google/Meta DON'T target the hard thing we were targeting.)

## One classical upgrade worth testing (expectation: marginal)
Regularized cost-volume depth (NOT patch-NCC only): Census/ZNCC cost + inverse-depth labels + SGM/graph-cut smoothness + LR/neighbor consistency + subpixel quadratic fit + edge-aware guided filter, then ego-center z-buffer render only where confidence passes. "Only plausible way to turn <0.2% confident into a useful mask without learning. BUT it may clean some facade pixels; it will NOT robustly fix BMW/graycar/storefront without new failures."

## What codex would do
**Ship `align + color=none/gain + E1.5` and write the residual honestly as "depth-accuracy-bound centralization error." Do NOT keep tuning copy-selection or blending.**
If the multi-day route is chosen, the highest-EV experiment: **overfit a strip-only Band-MPI / MVSNet on ONE AV2 log, supervised by leave-one-camera-out reprojection + sparse LiDAR, render only the seam strip to ego-center. If it can't beat L1 on the BMW after overfitting → the learned route is not worth scaling.**

> "Real wall for the deliverable, not a proof of impossibility for learned dense layered depth. Your clean next move is to ship the honest L1+ result, UNLESS the research goal specifically requires a multi-day learned depth method."
