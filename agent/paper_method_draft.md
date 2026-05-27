# Section 3: Method (Draft)

## 3.1 Problem Formulation

Given a synchronized set of $K$ ring cameras $\{C_1, \dots, C_K\}$ with calibrated intrinsics $\mathbf{K}_i \in \mathbb{R}^{3\times 3}$ and extrinsics $\mathbf{T}_i^{ego} \in SE(3)$ mapping camera frame to ego frame, the goal is to produce a single equirectangular projection (ERP) panorama $\mathbf{I}_{erp} \in \mathbb{R}^{H \times W \times 3}$ covering the full $360°$ horizontal field of view.

The standard pipeline projects each camera $C_i$ to a partial ERP slab $\mathbf{S}_i$ with a confidence weight $w_i$ per pixel (typically cosine-squared of angular distance from the optical axis), then blends overlapping slabs:

$$\mathbf{I}_{erp}(u, v) = \frac{\sum_i w_i(u, v) \cdot \mathbf{S}_i(u, v)}{\sum_i w_i(u, v) + \epsilon}$$

Multi-band variants [Burt & Adelson 1983] generalize this with band-frequency-dependent weights, but the fundamental averaging structure persists.

## 3.2 The Doubled-Feature Ghost Problem

For each ERP pixel $(u, v)$, the unit ray $\mathbf{d}_{ego}(u, v)$ in ego frame is computed by inverting the ERP convention:

$$\theta = \pi - \frac{(u + 0.5)}{W} \cdot 2\pi, \quad \phi = \frac{\pi}{2} - \frac{(v + 0.5)}{H} \cdot \pi$$
$$\mathbf{d}_{ego}(u, v) = [\cos\phi \cos\theta, \cos\phi \sin\theta, \sin\phi]$$

In **infinity-depth projection** (cameras at ego origin), this ray is rotated to camera frame and pinhole-projected to find the sampling pixel:

$$\mathbf{S}_i(u, v) = \mathbf{I}_i(\mathbf{K}_i \cdot R^{cam}_i \cdot \mathbf{d}_{ego}(u, v))$$

where $R^{cam}_i = (\mathbf{T}_i^{ego})^{-1}_{1:3, 1:3}$ ignores the camera translation $\mathbf{t}_i^{ego}$ (the $1.0-1.6$m forward and $0-0.21$m lateral offset from ego origin in AV2's ring rig).

The infinity assumption is correct when scene depth $\gg$ camera baseline (~0.21-0.26m between adjacent ring cams). For far objects, both cameras' projections of a single 3D point land at the same ERP pixel — the blend collapses to a clean average. **For near-field objects (within ~5m), the projections diverge.**

Concretely: a BMW parked 3m from the ego at the right-front seam is seen by both $C_{front\_center}$ (from the left) and $C_{front\_right}$ (from the right). With the cameras' 0.21m lateral baseline, the BMW's apparent position differs by:

$$\Delta_{erp} \approx \frac{\text{baseline} \cdot f_{erp}}{d_{object}} \approx \frac{0.21 \cdot 4096/(2\pi)}{3} \approx 46 \text{ pixels}$$

For a 4096-wide ERP. When both slabs are averaged with non-zero weights in the overlap zone (~212px wide), the BMW appears as two overlaid car bodies — the "doubled-feature ghost" (Fig 2a).

Crucially, this is a **view synthesis problem, not an alignment problem**: $C_{front\_center}$ sees the BMW's left side, $C_{front\_right}$ sees its right side. Even if depth were perfectly known (so both cameras' geometric projections landed at the same ERP pixel), the RGB contents at that pixel would differ. Their average is a chimera.

## 3.3 L1: Hard Camera Selection

We sidestep the view-mixing problem by selecting exactly one camera per ERP pixel via argmax over weights:

$$i^*(u, v) = \arg\max_i w_i(u, v), \quad \mathbf{I}^{HS}_{erp}(u, v) = \mathbf{S}_{i^*}(u, v)$$

When $w_i$ is the cosine-squared of the angular distance from the optical axis (zero outside the FOV, peaks at the principal point), this picks the camera whose optical axis is closest to the ERP ray direction. Geometrically equivalent to a Voronoi partition over ring cameras in angular space.

**Trade-offs**:
- (+) No view mixing → no doubled-feature ghost
- (+) Also eliminates "translucent ghost" (faint columns of content one camera sees but the other doesn't, blended at half strength)
- (−) Sharp seams at cam-cam boundaries (brightness jumps + texture cuts)

L1 sidesteps the view-mixing ghost, but exposes brightness and parallax seams that L2 and L3 then address.

## 3.4 L2: Joint Global Luminance HDR

Each camera's auto-exposure produces a different sensor gain. We observed on AV2 a mean luminance gap of 5.5 dB (max 9.1 dB) at seam pixels — visible as a sharp vertical brightness step after L1.

We solve for per-camera scalar luminance gains $g_i$ in log space. Let $\bar{Y}_i^{(i,j)}$ denote the mean luminance of slab $\mathbf{S}_i$ within the overlap region $\mathcal{O}_{ij} = \{(u, v) : w_i(u, v) > 0 \land w_j(u, v) > 0\}$. For matched overlap brightness:

$$g_i \cdot \bar{Y}_i^{(i,j)} = g_j \cdot \bar{Y}_j^{(i,j)} \iff \log g_i - \log g_j = \log \bar{Y}_j^{(i,j)} - \log \bar{Y}_i^{(i,j)}$$

This is a linear constraint per camera pair $(i, j)$. For the AV2 7-camera ring, we use all 7 adjacency pairs (including the back seam between rear_left and rear_right) and solve the over-determined linear system by least squares:

$$\hat{\mathbf{x}} = \arg\min_{\mathbf{x}} \|\mathbf{A}\mathbf{x} - \mathbf{b}\|^2_2 \quad \text{where } x_i = \log g_i$$

Each pair contributes a row in $\mathbf{A}$ with $+1$ at index $i$ and $-1$ at index $j$.

**Centering**: Because $\mathbf{A}$ is rank-deficient by 1 (only differences of $\log g$ are constrained), we either anchor one camera (e.g., $\log g_0 = 0$) or center the solution ($\sum_i \log g_i = 0$). Empirically, anchoring fails when the anchor camera is in shadow: all other gains exceed 1 and risk clipping. **We center the solution** (i.e. $\log g \leftarrow \log g - \overline{\log g}$ post-hoc) so the geometric mean of gains equals 1, spreading corrections symmetrically.

**Luminance-only**: We apply gains only to the Y channel in YCrCb space, preserving Cr/Cb. A prior per-channel RGB version accumulated chain drift of $g_{green}^{rear\_right} = 1.33$, producing a magenta cast on the chain tail. Y-only correction is structurally cast-free.

Finally, clip gains to $[0.5, 2.0]$ to guard against degenerate overlaps.

## 3.5 L3: Per-Overlap Farneback OF Chain Warp

After L1+L2, residual misalignment at seams (lane lines, road markings) reflects the geometric parallax between cameras for near-field objects. Rather than estimate depth — which we found unreliable for view-synthesis reasons (Section 4) — we **use the two cameras themselves as ground truth**: dense optical flow in their overlap zone encodes the exact parallax displacement.

For each adjacent pair $(C_a, C_b)$, we compute Farneback OF [Farneback 2003] from the slab of $C_b$ to the slab of $C_a$ within $\mathcal{O}_{ab}$:

$$\mathbf{F}_{b \to a}(u, v) = \text{Farneback}(\mathbf{S}_b|_{\mathcal{O}_{ab}}, \mathbf{S}_a|_{\mathcal{O}_{ab}})$$

We Gaussian-smooth the flow ($\sigma = 5$ px) and dilate the overlap mask by 20 px so the warp tapers smoothly to identity outside $\mathcal{O}_{ab}$:

$$\mathbf{S}'_b(u, v) = \mathbf{S}_b(u + F_{b \to a}^x, v + F_{b \to a}^y)$$

**Chain composition**: We chain warps in both directions from the front-center anchor:
- CCW: front_center $\to$ front_left $\to$ side_left $\to$ rear_left
- CW: front_center $\to$ front_right $\to$ side_right $\to$ rear_right

Each step warps the new camera to align with the **previously-warped** preceding camera (not the original). This propagates alignment around the ring.

**Back-seam closure**: The two chains both terminate at rear cameras, leaving the back seam uncorrected. We add one more OF warp aligning rear_right (CW-end) to rear_left (CCW-end) within their back-seam overlap, closing the loop.

## 3.6 Pipeline Order

The three layers are applied in the order: $\mathbf{S} \xrightarrow{L2} \mathbf{S}^{HDR} \xrightarrow{L3} \mathbf{S}^{HDR,OF} \xrightarrow{L1} \mathbf{I}_{erp}$.

- L2 before L3 so Farneback flow estimation doesn't lock onto brightness mismatches as if they were features.
- L3 before L1 so per-pixel selection sees aligned slabs.
- L1 last as the final per-pixel pick.

## 3.7 Implementation Notes

The full pipeline is ~200 lines of Python using only `cv2` and `numpy`:
- L1 hard_select: 5 lines (numpy argmax + take_along_axis)
- L2 HDR: ~20 lines (lstsq + YCrCb conversion)
- L3 OF: ~40 lines per pair-warp, applied 7 times (6 chain + 1 back-seam)

End-to-end runtime: ~50 seconds per anchor at 2048×4096 ERP on a Tesla T4 (mostly CPU-bound for OF). Multiband baseline: ~6 seconds. The 8× slowdown reflects the cost of OF; without L3, ~13 seconds (just L1+L2).
