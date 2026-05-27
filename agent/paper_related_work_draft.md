# Section 2: Related Work (Draft)

## 2.1 Classical Image Stitching

The foundational pipeline for combining multiple cameras into a panorama dates to Brown & Lowe's AutoStitch [2003], which uses SIFT features [Lowe 2004] for matching, RANSAC [Fischler & Bolles 1981] for outlier rejection, bundle adjustment for global alignment, and Burt-Adelson multi-band blending [1983] for the final composition. OpenCV [Bradski 2000] provides a reference implementation in `cv2.detail.Stitcher`, used by Hugin and other widely-deployed panoramic photography tools. These methods assume **pure rotation** between cameras — appropriate for handheld panoramas but ill-suited to rigid multi-camera rigs where translation produces visible parallax for near-field objects.

Recent work has extended classical stitching to handle small translations: Szeliski [2007]'s seminal survey covers seam optimization, exposure compensation, and content-preserving warps; the Image Composition Editor [Microsoft 2009] uses content-aware blending; and more recent academic work explores graph-cut seam routing [Kwatra et al. 2003, Agarwala et al. 2004] to find the least-visible seam path through overlap zones. We compare against multi-band blending as our primary baseline, and discuss graph-cut as a complementary alternative to our L1 hard selection in Section 5.

## 2.2 Depth-Aware Panorama Methods

Several lines of work explicitly model the depth of overlap-region content to correct parallax:

- **Plane-sweep stereo**: Estimate dense depth from the two-view stereo pair (e.g., Semi-Global Block Matching [Hirschmüller 2008] in OpenCV's `cv2.StereoSGBM`), then re-project both cameras to a common ERP using the inferred depth.
- **LiDAR splat**: Project sparse LiDAR returns to image space, propagate via kNN or learned completion [Tang et al. 2020], use as per-pixel depth for projection.
- **Monocular depth networks**: DPT [Ranftl et al. 2021], MiDaS [Ranftl et al. 2020], Depth Anything V2 [Yang et al. 2024] estimate dense depth from a single image; use as projection depth.

We tested representative members of each family on our problem (Section 4.2 N1 family ablation) and found that **none eliminate the doubled-feature ghost**. The reason — view-mixing, not view-disagreement — is our central thesis (Section 3.2).

## 2.3 Multi-Camera Rigs for Autonomous Driving

The autonomous-driving community uses multi-camera rigs extensively for perception. Waymo [Sun et al. 2020], nuScenes [Caesar et al. 2020], and Argoverse 2 [Wilson et al. 2021] each provide ring-camera datasets with calibrated intrinsics and extrinsics. The Argoverse 2 sensor split includes 7 ring cameras with substantial overlap, making it a natural testbed for panorama composition.

To our knowledge, **explicit ring-camera panorama composition has received limited attention** in the AV literature. Most downstream tasks (object detection, segmentation, motion forecasting) operate on individual camera images or birds-eye-view representations [Philion & Fidler 2020, BEVFormer 2022]. Recent world-model work (e.g., GAIA-1 [Hu et al. 2023], Sora-style video models) benefits from panoramic inputs but tends to use ad-hoc stitching pipelines without published methodology. Our work fills this gap with an honest analysis of what does and does not work for ring-camera panoramas.

## 2.4 View Synthesis and Neural Rendering

The "right" solution to the view-mixing problem is **view synthesis**: given $K$ camera views of a scene, synthesize a novel view from any desired viewpoint. This is the realm of NeRF [Mildenhall et al. 2020], 3D Gaussian Splatting [Kerbl et al. 2023], and their many extensions. For our problem, we would want to synthesize the panoramic view "between" adjacent ring cameras, eliminating both the doubled ghost and the seam visibility.

However, view synthesis methods have substantial costs:
- **Per-scene training**: Each scene requires per-scene optimization (10s of minutes per scene for NeRF, seconds-to-minutes per scene for 3DGS).
- **Multi-frame data**: Best results require many input views per scene; a single ring-camera anchor (7 images) is at the low end of the practical range.
- **Generalization gap**: Trained on specific scenes, struggle with novel data without retraining.

Recent work on **feed-forward view synthesis** (e.g., PixelNeRF [Yu et al. 2021], MVSplat [Chen et al. 2024]) addresses some of these issues, but to our knowledge has not been demonstrated for 360° panoramas from sparse ring cameras at the production scale of AV datasets (~10 Hz, ~1M frames). Seam360GS [recent year] is a noted exception that applies 3DGS specifically to ring-camera seam removal, but at non-real-time cost.

Our contribution is complementary: we provide a classical-CV baseline that runs in 50 seconds per panorama with no learning, and that depth-based or learning-based methods should compete against to demonstrate value.

## 2.5 HDR / Exposure Compensation

OpenCV's `cv2.detail.ExposureCompensator` implements gain-based exposure compensation [Brown & Lowe 2003] via least-squares solve over pairwise gain ratios. Our L2 layer adapts this for ring cameras with three modifications: (1) Y-channel-only correction to preserve chroma, (2) log-space centering to prevent over-amplification, (3) inclusion of the back-seam constraint for the ring topology. We discuss the design choices in Section 5.3.

## 2.6 Optical Flow

Farneback's polynomial expansion method [Farneback 2003] is a classical dense optical flow algorithm widely used in production (`cv2.calcOpticalFlowFarneback`). It excels in textured regions and degrades gracefully in textureless ones. More modern OF methods (RAFT [Teed & Deng 2020], FlowFormer [Huang et al. 2022]) achieve higher accuracy but at much higher cost. We use Farneback for L3 to maintain real-time performance; replacing it with RAFT is an obvious extension for higher-quality outputs at slower runtime.

---

## Citations to add (placeholder)

- Brown & Lowe 2003: "Recognising panoramas" (or 2007 AutoStitch journal version)
- Lowe 2004: "Distinctive image features from scale-invariant keypoints" (SIFT)
- Burt & Adelson 1983: "A multiresolution spline with application to image mosaics"
- Fischler & Bolles 1981: "Random sample consensus" (RANSAC)
- Szeliski 2007: "Image alignment and stitching: A tutorial"
- Kwatra et al. 2003: "Graphcut textures"
- Agarwala et al. 2004: "Interactive digital photomontage"
- Hirschmüller 2008: "Stereo processing by semiglobal matching"
- Tang et al. 2020: "Learning guided convolutional network for depth completion"
- Ranftl et al. 2020/2021: MiDaS / DPT papers
- Yang et al. 2024: "Depth Anything V2"
- Sun et al. 2020: Waymo Open Dataset
- Caesar et al. 2020: nuScenes
- Wilson et al. 2021: Argoverse 2
- Philion & Fidler 2020: Lift-Splat-Shoot
- BEVFormer 2022
- Hu et al. 2023: GAIA-1
- Mildenhall et al. 2020: NeRF
- Kerbl et al. 2023: 3D Gaussian Splatting
- Yu et al. 2021: PixelNeRF
- Chen et al. 2024: MVSplat
- Seam360GS (need to find exact citation)
- Farneback 2003: "Two-frame motion estimation based on polynomial expansion"
- Teed & Deng 2020: RAFT
- Huang et al. 2022: FlowFormer
- Bradski 2000: OpenCV
