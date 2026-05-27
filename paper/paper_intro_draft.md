# Section 1: Introduction (Draft)

Autonomous vehicles rely on a multi-camera ring sensor rig for 360° visual perception. The Argoverse 2 sensor dataset [Wilson et al., 2021] equips each vehicle with 7 ring cameras spanning the full horizontal field of view, complemented by LiDAR and stereo cameras. A common downstream need — particularly for training neural world models — is to compose these 7 partial views into a single equirectangular projection (ERP) panorama: a 360° image suitable for video prediction, occupancy estimation, and simulation pipelines.

This composition is conceptually a classical image stitching problem [Brown & Lowe 2003, Szeliski 2007], but the autonomous-vehicle setting differs from traditional handheld panorama capture in three crucial ways:

1. **Fixed but non-trivial baseline**: Adjacent ring cameras are mounted 0.21-0.26 m apart on the vehicle frame — too far for pure-rotation panoramas, too close for stereo depth. The baseline-to-depth ratio is significant for any object within ~5 m of the ego vehicle.
2. **Hard real-time / batch budget**: ~10 Hz of synchronized 7-camera frames must be processed at production scale (millions of panoramas per dataset). Methods that take minutes per frame (e.g., NeRF-based view synthesis) are non-starters.
3. **Auto-exposed, variable lighting**: Each camera independently adjusts gain and white balance; we measure mean luminance gaps of 5.5 dB across adjacent cameras on AV2 (max 9.1 dB).

Naive cosine-squared feathering or multi-band blending [Burt & Adelson 1983] of overlapping camera projections produces a characteristic failure on near-field objects: the **doubled-feature ghost**. A parked car at 3 m, seen by two adjacent cameras at slightly different angles, appears as two overlaid car bodies after blending (Fig. 1, left). The ghost is exacerbated by the auto-exposure mismatch, which adds a visible brightness step at every camera-to-camera seam.

A natural assumption is that this is a *depth estimation* problem: given correct depth, the two cameras' projections of the same 3D point should land at the same ERP pixel, and the blend would be clean. We tested four depth-based pipelines (Section 4.2): single convergence radius, LiDAR sparse splat with kNN fill, LiDAR + graphcut hard-seam routing, and dense monocular depth via Depth Anything V2. **All four failed** to eliminate the doubled-feature ghost. The reason is structural: the two cameras see *different content* (cam A sees the BMW's left side, cam B sees its right side), and no amount of geometry alignment can reconcile views that are genuinely incompatible. This is a **view synthesis** problem, not an alignment problem.

We propose to *sidestep* the view-mixing problem rather than solve it. Our contribution is a three-layer pipeline built entirely from classical computer vision:

- **L1 Hard camera selection**: argmax of cos²-weighted angular distance per ERP pixel. Each ERP pixel comes from exactly one camera; the view-mixing ghost is eliminated by construction.
- **L2 Joint global luminance HDR**: per-camera scalar gain on the Y channel of YCrCb, computed via least-squares solve over all 7 ring adjacency pairs (including the back seam). Centered in log-space to avoid amplification when the natural anchor is in shadow.
- **L3 Per-overlap Farneback optical flow chain warp**: dense flow between each adjacent camera's ERP slabs in their overlap zone *uses the two cameras themselves as ground truth* for parallax displacement, then warps to align. Chained from front-center in both directions, with a back-seam closure.

The full pipeline runs in 50 seconds per anchor at 2048×4096 ERP resolution on a Tesla T4 GPU, with no dependencies beyond OpenCV. On the Argoverse 2 validation set (5 logs, 575 anchors), it produces visually ghost-free panoramas where multi-band blending shows clearly doubled features, and reduces the mean cross-camera seam luminance gap by 10.8%.

Our contributions:
1. We identify the doubled-feature ghost as a **view synthesis** problem, not a depth problem, and document four negative ablations to support this claim.
2. We propose a three-layer basic-CV pipeline that resolves the ghost without depth, with each layer addressing a distinct artifact (view mixing, exposure mismatch, spatial parallax).
3. We release the implementation as a drop-in `blend_mode` argument to a standard ring-camera stitching pipeline, with backwards compatibility to multi-band blending.
4. We argue that depth-based ring-camera panorama methods must justify themselves *against hard selection*, not just against multi-band blending — and provide a strong basic-CV baseline for that comparison.
