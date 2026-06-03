# Reason: next exploration after DB-14 old-r008 rejection

Question: After DB-14 shows blind reuse of the old v14 r008 fixed vertical-strip mask fails visually on G/A1/BEST, what should the next exploration path be?

## Round 1 - Positions

Position ARG-7C2F: Run DB-21 next: derive a current-base-aligned thin seam mask, require visual overlay approval before GPU, then run a tiny DiT360 tau{5,8} test on G only.

Position ARG-91BD: Pivot first to cube-space/CubeComposer-style continuity: convert ERP to cubemap/cube faces and handle seam continuity there before any more DiT360 runs.

Position ARG-44A9: Stop generative seam repair and return to Street-View/Surround360-style optical-flow and global-warp methods, because commercial systems solve seams with geometry/flow rather than diffusion.

Position ARG-E603: Stop T1 seam chasing for now and assemble the best presentable deliverable: current best base + proven sky-only outpaint, with the wavy ground seam honestly labeled as the residual floor.

## Round 1 - Critiques

ARG-7C2F critique: Strong assumption is that a better mask fixes the main failure. DB-14 proves the old mask is wrong, but not that DiT360 can faithfully straighten a lane/curb once the mask is correct. Logical strength: 8/10.

ARG-91BD critique: Strong assumption is that CubeComposer seam ideas transfer to a deterministic repair task. The source says CubeComposer is perspective-video-to-360 generation with cube-aware context/padding/blending; it is not an AV multi-camera stitcher or a local seam repair model. Full pivot risks large setup cost before a single falsifiable image. Logical strength: 6/10.

ARG-44A9 critique: Google and Meta do rely on optical flow, but the local project already hit textureless/occlusion floors in flow-derived methods. Google also discards low-confidence flow correspondences and solves a global under-constrained warp, which may still fail when our overlap has too little reliable structure. Logical strength: 6/10.

ARG-E603 critique: It is honest and low risk, but it may prematurely freeze the one user-visible defect that motivated the current A100 session. It should be a fallback deliverable, not the next exploration if a cheap DB-21 falsification exists. Logical strength: 7/10.

## Round 1 - Rebuttals

ARG-7C2F rebuttal: Narrow the claim: DB-21 is not "DiT will work"; it is the cheapest falsifier of the mask-alignment hypothesis. Overlay-first kill criteria prevent another blind GPU sweep.

ARG-91BD rebuttal: Narrow the claim: do not run CubeComposer full model yet. Inspect/borrow cube-face representation and boundary blending ideas as design guidance, possibly for mask construction or cube-local diagnostics.

ARG-44A9 rebuttal: The commercial-method insight should remain in the evidence model: flow works where reliable correspondence exists; our failure is likely sparse/ambiguous overlap and occlusion. Use it to constrain DB-21 masks, not to reopen broad flow work.

ARG-E603 rebuttal: Accept fallback role. If DB-21 fails, the deliverable path should become current-best + sky-only outpaint rather than more seam patching.

## Round 1 - Judgment

Judge A logic: ARG-7C2F 8.5, ARG-91BD 6.5, ARG-44A9 6.5, ARG-E603 7.5. Strongest: ARG-7C2F.

Judge B evidence/falsifiability: ARG-7C2F 9, ARG-91BD 6, ARG-44A9 7, ARG-E603 8. Strongest: ARG-7C2F.

Judge C practical applicability: ARG-7C2F 9, ARG-91BD 5.5, ARG-44A9 6, ARG-E603 8. Strongest: ARG-7C2F.

Convergence check: Not complete. ARG-7C2F leads but must answer whether cube-space should precede mask generation.

## Round 2 - Critiques

ARG-7C2F critique: It could still generate a mask in ERP coordinates that is distorted near cube/ERP boundaries, repeating the "vertical strip" pathology in a narrower form. Logical strength: 8/10.

ARG-91BD critique: Its useful part is representation-level, not model-level. If it demands cloning/running Wan/CubeComposer before DB-21, it is an expensive detour from a local falsifiable test. Logical strength: 6/10.

ARG-44A9 critique: It frames Google/Meta as proof that our rig should be solvable, but those systems rely on different camera geometry, strong sync/calibration, and enough overlap texture; Meta explicitly notes optical flow remains ill-posed under occlusion. Logical strength: 6.5/10.

ARG-E603 critique: It maximizes presentability but not discovery. The user's explicit goal is to keep exploring while A100 is available, so stopping before DB-21 leaves a cheap hypothesis untested. Logical strength: 7.5/10.

## Round 2 - Rebuttals

ARG-7C2F rebuttal: Add a cube-aware precheck without a full CubeComposer pivot: inspect the defect in ERP crop and, if the seam sits near problematic projection distortion, also render a cube-face/rectilinear view for mask overlay. GPU still waits for visual overlay approval.

ARG-91BD rebuttal: Concede full-model run is not first. Keep CubeComposer as a source for DB-22 only if DB-21 overlay or output suggests ERP geometry is the blocker.

ARG-44A9 rebuttal: Concede commercial methods define an upper-bound style, not a drop-in recipe. The actionable import is confidence-gated correspondences and subtle global regularization, not another broad flow rewrite.

ARG-E603 rebuttal: Concede it is the fallback product path after DB-21, not the immediate exploration path.

## Round 2 - Judgment

Judge A logic: ARG-7C2F 9, ARG-91BD 7, ARG-44A9 7, ARG-E603 8. Strongest: ARG-7C2F.

Judge B evidence/falsifiability: ARG-7C2F 9, ARG-91BD 7, ARG-44A9 7, ARG-E603 8. Strongest: ARG-7C2F.

Judge C practical applicability: ARG-7C2F 9, ARG-91BD 6.5, ARG-44A9 6.5, ARG-E603 8. Strongest: ARG-7C2F.

Convergence check: Converged by threshold. ARG-7C2F top scores are all >=8 and the main rebuttal is answered by adding cube-aware overlay precheck before GPU.
