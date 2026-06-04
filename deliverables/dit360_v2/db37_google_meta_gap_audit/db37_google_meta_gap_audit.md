# DB37 Google/Meta Seam-Mechanism Gap Audit

Status: closed-no-new-local-repair

## Question

After DB35 rejected post-hoc donor patching and DB36 rejected an ultra-narrow DiT360 ground seam mask, is there a Google/Meta/StreetView-style seam mechanism that the project has not tested and that can plausibly solve the `G_bmw_pano` long red-line / right white-line seam?

## Sources Checked

- Google Research, "Seamless Google Street View Panoramas" (2017): https://research.google/blog/seamless-google-street-view-panoramas/
- Google Research, "Jump: Virtual Reality Video" (SIGGRAPH Asia 2016): https://research.google/pubs/jump-virtual-reality-video/
- Meta Engineering, "Introducing Facebook Surround 360" (2016): https://engineering.fb.com/2016/04/12/video-engineering/introducing-facebook-surround-360-an-open-high-quality-3d-360-video-capture-system/
- Facebook archive GitHub, Surround360 repo: https://github.com/facebookarchive/Surround360
- Li et al., "A Unified Framework for Street-View Panorama Stitching" (Sensors 2017): https://www.mdpi.com/1424-8220/17/1/1

## Mechanism Map

| Production mechanism | What the source claims | Project equivalent already tested | DB37 verdict for BMW G seam |
|---|---|---|---|
| Optical-flow seam repair with confidence filtering | Google Street View computes dense overlap correspondences, downsamples them, and discards low-structure correspondences before global optimization. | A1 view interpolation, `_seamroute.py` flow-align, virtual-center select, DB25 flow-reliability pack. | Already tested in spirit. DB25 shows the key right/dark-wall pair has only 10.5% FB-flow reliability and the ROI is 62.3% near-ground with only 9.4% LiDAR support. A production system would abstain from many of these correspondences, not hallucinate a clean line. |
| Global regularized warp | Google solves a spatially regularized spline warp with Ceres so seam repair stays subtle and avoids local wobble artifacts. | `_seamroute.py` align + hard select, DB15 line-cost reroute, DB24/25 Google-style diagnosis. | A missing exact implementation detail, but not a missing evidence source. Google's own premise is reliable overlap correspondences; BMW's target ROI lacks them. A global warp would be under-constrained and risks the same wobble/geometry drift DB24/26 warned about. |
| Pairwise image interpolation plus compositing | Google Jump reduces ODS stitching to pairwise image interpolation followed by compositing, using custom optical flow and temporal coherence. | A1 `view_none`, `_seamroute.py` virtual-center select, DB13 learned/single-center checks. | Already explored. `view_none` ghosts/parallax; single-source pick removes ghost but leaves the low-evidence seam. Jump's assumptions include a purpose-built VR rig and video/temporal coherence; BMW is a sparse non-co-located AV still-frame seam problem. |
| Meta Surround360 virtual-view synthesis | Meta uses calibrated hardware, ISP/color correction, lens correction, bundle-adjusted extrinsics, optical flow disparity, virtual camera synthesis, and final compositing. | color/gain correction, calibration-based projection, flow pair tests, virtual-center select, object gate, DB13/DB25 occlusion analysis. | Already tested in equivalent components. Meta explicitly names occlusion as a remaining difficulty, mitigated by multiple cameras and time-varying capture. The BMW right seam is exactly a weak co-visibility/occlusion/near-ground case. |
| Warping + color correction + optimal seam line + blending | Street-view stitching literature frames the pipeline as warping, color correction, optimal seamline detection, and blending, while noting non-common centers/depth differences cannot be precisely aligned. | `_seamroute.py`, DB15/16/17 non-DiT attempts, DB26 photometric attenuation, DB35 donor patch. | Exhausted for this ROI. Color/blending/donor changes either do not remove the line or create fake/smudged ground. |
| Generative thin seam repair | Not a core Google/Meta source-faithful seam mechanism; it is our DiT360 route. | DB14/21/23/36. | Rejected for ground seams. DB36 proves that even a 0.816% ultra-narrow mask can create fake road slabs/holes. DiT remains useful for sky/out-of-FoV, not trusted ground seam repair. |

## Direct Answer

DB37 does not find a new defensible local seam repair for the `G_bmw_pano` red-line/right-line failure. Google/Meta-style systems mainly succeed when reliable overlap correspondences, calibrated capture, more camera/time redundancy, and subtle global warps are available. The BMW target ROI is exactly the counter-case: near-ground, low texture, sparse LiDAR, weak right-pair flow, and partial occlusion/co-visibility.

## Remaining Actionable Path

The only source-faithful path still defensible is upstream selection or capture-side improvement:

1. Prefer the cleaner DB28/DB32 source sidestep when the deliverable can use a different anchor.
2. If the exact G scene must be used, label the red-line/right-line seam as an evidence floor instead of trying more post-hoc ground generation.
3. Only reopen a local seam repair if a new evidence source appears: denser depth, more temporal frames with the same scene, raw camera overlap that raises the key pair's FB-flow reliability, or a different capture rig with stronger overlap.

## Kill Decision

No DB38 local repair test is opened from this audit. A CPU diagnostic would only re-run already failed families unless new evidence is added.
