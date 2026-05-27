# Sections 5 + 6: Discussion + Conclusion (Draft)

## 5. Discussion

### 5.1 Why depth-based methods fail

The most surprising negative result in this paper is that *none* of our four depth-based pipelines reduced the doubled-feature ghost. We initially expected at least one of them to work — given access to ground-truth LiDAR depth, dense monocular depth, or sparse-then-densified depth — but all failed.

The unifying explanation is **view-mixing**, not view-disagreement: when two cameras see the same near-field 3D object from sufficiently different angles, they capture *different content*. Camera A might see the left side of a car, including its driver window; camera B might see the right side, including its passenger door. There is no projection geometry that can reconcile "driver window" and "passenger door" into a single ERP pixel — the answer to "what is at this ERP pixel?" is genuinely *both*, depending on perspective.

Multi-band blending averages the two, producing a chimera (two overlaid car bodies). Depth-corrected projection brings them to the same ERP pixel but the averaging still produces a chimera. The only resolutions are:
1. **Select one** (our L1): pick a single camera's content per pixel, accept the visible seam.
2. **True view synthesis** (NeRF, 3DGS, Seam360GS): synthesize a "between" view that smoothly interpolates the two camera viewpoints. This is the right answer in principle but currently infeasible at production scale (minutes per frame, requires multi-frame data).

We argue that L1 is the right intermediate point for current systems: it eliminates the ghost at the cost of seams, which classical CV (L2, L3) can then partially close. View synthesis remains future work for cases where seams are unacceptable.

### 5.2 The role of optical flow

L3 (Farneback OF warp) is the layer that introduces the most variance in our results. On some anchors it visibly improves lane-line continuity at seams; on others the improvement is barely perceptible. We attribute this to two factors:
- **Parallax magnitude varies**: a 3m-distance object produces a 46-pixel ERP shift, but a 30m-distance building produces only 5 pixels. OF correction is biggest where parallax is biggest, but those are often dominated by other failure modes (textureless road surfaces, occlusions).
- **OF reliability varies**: textureless overlaps (sky, smooth pavement) yield noisy flow fields. We mitigate with Gaussian smoothing (σ=5 px) and overlap masking, but the OF can still degenerate.

A more principled L3 alternative is Brown-Lowe feature matching (SIFT/ORB + RANSAC), which fails gracefully in textureless regions by simply not warping (no features → no correspondences → identity warp). We leave this as a robustness ablation for future work.

### 5.3 The role of HDR

L2 (joint global luminance HDR) is the most quantitatively impactful layer: 10.8% mean seam-gap reduction. Two design choices matter:

**Luminance-only vs per-channel**: A natural first attempt is to solve per-channel RGB gains. We found this produces visible chroma cast on the chain tail (rear cameras): green gains accumulated to 1.33×, shifting the back half of the panorama toward magenta. Luminance-only (YCrCb Y channel) is structurally cast-free.

**Centered vs anchored**: Anchoring the front-center camera at gain=1.0 fails when that camera is in shadow — all other gains exceed 1.0 and risk clipping. Centering the log-gains around 0 (geometric mean of gains = 1.0) spreads corrections symmetrically. The centered version "robustly $\geq 0$" improves seam-gap on every test anchor, while the anchored version made the worst case 7% worse.

We did not solve for chroma equalization (Cr/Cb offsets), because cross-camera chroma differences in overlap can reflect *real* scene differences (one camera sees a green tree, the other sees a gray wall). Equalizing these would shift colors incorrectly. A robust chroma solver would need regularization toward zero offset — left for future work.

### 5.4 Limitations

- **Single-anchor processing**: Each panorama is rendered independently. For video output, per-anchor HDR gains can flicker as exposures change frame-to-frame. Temporal smoothing of gains would help.
- **Very-near-field (<1m)**: For objects under 1m, parallax exceeds the OF search window (we use Farneback win=31 px). Such close objects (e.g., curbs in front of the ego) may still show seam misalignment.
- **Strong sun flare**: When one camera captures direct sun flare, its luminance gain is computed against an unrepresentatively bright overlap region. HDR may then over-darken that camera. The 0.5-2.0 gain clip helps but doesn't fully prevent.
- **Back-seam OF residual**: Our back-seam closure helps but doesn't fully reach the cleanliness of the front-center-adjacent seams (which have direct anchor reference). A joint OF solver would close the gap.

### 5.5 Broader applicability

While we evaluate on AV2 autonomous-vehicle ring cameras, the three-layer recipe applies to any rigid multi-camera ring rig:
- **Surveillance**: Bank cameras, retail security, sports venue tracking
- **Mobile robotics**: Warehouse robots, agricultural drones, planetary rovers
- **Cinematography**: 360° VR capture, multi-cam events
- **Photogrammetry**: When ring rigs are used for object scanning

The only AV-specific assumption is our 7-camera adjacency topology; any ring of $K \geq 3$ cameras with computable cos²-feathered slabs can use the same L1+L2+L3 logic.

## 6. Conclusion

We presented a three-layer basic-CV pipeline for multi-camera 360° panorama stitching that resolves the doubled-feature ghost characteristic of multi-band blending without resorting to depth estimation. The key insight is that the ghost is a **view-mixing problem**, not an alignment problem: two cameras seeing genuinely different content cannot be reconciled by any projection geometry. Hard camera selection sidesteps the mix, joint global luminance HDR closes the brightness step, and per-overlap optical flow chain warp aligns spatial parallax.

The complete implementation is ~200 lines of OpenCV + NumPy, runs in 50 seconds per anchor at 2048×4096 ERP on a Tesla T4 GPU, and produces qualitatively ghost-free panoramas on the Argoverse 2 validation set across 5 diverse driving scenes. We document four depth-based negative ablations to support the view-mixing-not-depth thesis, and provide the implementation as a backward-compatible `blend_mode` argument to a standard ring-camera stitching pipeline.

Our approach offers a strong baseline for future work in autonomous-vehicle panorama composition. Depth-based methods, view synthesis, and learning-based stitchers should be evaluated *against hard selection* — not just against multi-band blending — to demonstrate they add value beyond ghost elimination.
