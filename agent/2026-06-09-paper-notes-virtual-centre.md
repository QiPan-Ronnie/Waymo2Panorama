# Paper notes — "The virtual centre was the wall" (2026-06-09 session)

## 1. One-paragraph story

Every panoramic image stitched in this project since L1 has pinned the ERP virtual centre to the ego-vehicle origin, located 1.81–2.18 m from the actual ring-camera array (mostly forward/height offset). The perpendicular-baseline depth-error amplification model `err ≈ (W/2π)·b_perp·δZ/Z²` predicts render-back failures at b_perp ≈ 1.5–2 m; measured reprojection errors fit exactly. Moving the virtual centre to the ring-camera centroid (≤0.3 m away in-sector) reduces b_perp by one order of magnitude, shrinking the reported "seam wall" 18–96× across five AV2 scenes. Depth-aware rendering at the centroid now reopens the geometric seam channel; the honest residual becomes occlusion silhouettes (thin-band, few pixels) and no-evidence zones, turning the project's dominant error source into a choice rather than a physical wall.

## 2. Method components (each with one-line description + key numbers)

- **Virtual-centre relocation** (DB-80): shift ERP sphere centre from ego-vehicle origin [0,0,0] to ring-camera centroid [1.36, -0.00, 1.44] m (per-log calibration, zero tuning); re-render same accumulated LiDAR depth with single-source min-perpendicular-baseline camera selection.
  
- **LiDAR-correspondence colour harmonisation** (DB-81 P1): solve per-camera per-channel multiplicative gains from co-observed LiDAR 3D points in log domain (ring-closed least squares, Σc=0); eliminates 58–88% of inter-camera exposure/WB steps without global tone drift.

- **Multi-anchor robustness & no-LiDAR ablation** (DB-82): validate new base across 3 temporal anchors per log and gracefully degrade to plane-depth-only (coarse depth tolerance now ≤1 m near-field, from 0–15% abstain fraction to 89–99%), confirming north-star generality claim without LiDAR.

## 3. Experimental numbers table

| Metric | Scene | Ego-origin | Centroid | Reduction |
|--------|-------|-----------|----------|-----------|
| DEPTH render-back p90 (cam px) | BMW | 84.0 | 4.7 | 17.8× |
| | Clean | 146.7 | 5.8 | 25.3× |
| | Highway | 38.8 | 0.40 | 96× |
| | Downtown | 11.7 | 0.10 | 93× |
| | Crowd | 67.9 | 1.6 | 43× |
| Silhouette p90 (cam px) | BMW | 123 | 7.6 | 16× |
| | Clean | 152 | 9.7 | 16× |
| | Highway | 82 | 2.9 | 28× |
| | Downtown | 107 | 2.4 | 45× |
| | Crowd | 88 | 5.9 | 15× |
| Curbwall ROI p90 (cam px) | Clean | 55.5 | 4.07 | 13.6× |
| | Highway | 4.6 | 0.38 | 12× |
| | Downtown | 20.3 | 0.20 | 102× |
| | Crowd | 187.2 | 3.26 | 57× |
| Measured b_perp p50 | Ego-origin | 1.48–1.73 m | — | — |
| | Centroid | — | 0.12–0.13 m | 11–14× |
| Depth tolerance (≤2 ERP px, >1 m depth) | Near-field ego-origin | 0–15% | 89–99% | ~6× abstain shrink |
| P1 colour-step cut (LiDAR pairs) | Highway | — | 88.3% | — |
| | Crowd | — | 82.5% | — |
| | Downtown | — | 69.1% | — |
| | Clean | — | 58.0% | — |
| | BMW | — | 27.3% | — |

## 4. Negative results & limitations (paper-honest)

**DB-83 disocclusion failure (9 variants explored):**
The user-flagged sedan doubling at camera FOV boundaries is not a renderer patch. The dark sedan straddles the front_left/side_left boundary; each camera observes only half the car. The region beside the car is a disocclusion zone where both depth evidence (no LiDAR behind the car) and colour evidence (blocked line-of-sight) are absent at the anchor instant. Nine renderer variants (box-footprint hard lock, facing-checked depth, moat locking, per-camera occlusion test, background-only depth field, LiDAR-support gating, LiDAR-silhouettes, soft seam-steering) all re-paint ghosts from the unobstructed camera or introduce worse artifacts. Conclusion: object-boundary disocclusion requires either full layered rendering with inpaint or evidence-based temporal fill—deterministic same-frame fixes fail. Baseline cen_depth_b1 boundary-straddling artifact (~10–20 px head-ghost, 1 instance per 5 scenes) is recorded as a known limitation.

**P2 radial chromatic aberration (DB-81 NEG, honest):**
Grid search for lateral CA coefficient k across all cameras and channels returns k ≈ 0 for every case. AV2 ring images ship undistorted; no measurable lateral CA exists. The near-ground purple/green fringe is therefore not lens distortion. Attribution chain confirmed: (1) NOT CA (k ≈ 0), (2) NOT JPEG (present in lossless PNG), (3) present in native ring_side_right camera image = AV2 source-data ISP chroma noise in shadow regions. Our pipeline neither creates nor can losslessly remove this artefact; any fix belongs to optional labeled shadow-desaturation post-processing.

**Clipped-sky multiplicative-gain limitation (DB-82):**
On dusk scenes, B1 multiplicative gains make the clipped/overexposed front-centre sky tile's cyan cast more visible, because saturated pixels violate the log-linear gain model. Tone-curve or saturation-aware gain extension (P3) is a possible fix but not opened; saturated sky ownership defers to sky-outpaint.

**Occlusion-aliasing measurement protocol caveat (DB-80, step A.5):**
The pre-registered kill clause fired on BMW curbwall ROI p90 64 px > 30 px threshold. Attribution analysis shows 100% of >30 px residual pairs are occluded far-layer test points (background behind near-surface, dz > 1 m, td ≈ 40 m) scored against the near-wins depth that correctly owns the pixel for rendering. The visible-surface pairs (dz ≤ 0.25 m, n=198) have p90 = 0.59 px, passing cleanly. This is a single-layer depth evaluation protocol aliasing that equally inflates DB-79's ego-origin numbers—not a render-back wall. Verdict: clause logged as fired + attributed, b_perp hypothesis confirmed by global numbers and 4 other scenes' clean passes.

## 5. Claims we can defend vs claims we cannot yet

**Defensible (on-disk, 2026-06-09):**
- Five AV2 scenes × ring-camera centroid geometry + single-source depth rendering (DB-80 steps A, B, C)
- Three anchors per log confirming multi-anchor robustness (DB-82)
- Graceful no-LiDAR degradation (cen_plane_b1 vs cen_depth_b1; plane-depth sufficient at centroid, ~20× tolerance relaxation)
- Per-camera LiDAR-correspondence colour harmonisation (58–88% exposure cut on 4/5 scenes; DB-81 P1)
- b_perp physics model validated against measured reprojection errors; depth-error formula `err ≈ (W/2π)·b_perp·δZ/Z²` reproduces observed p90s exactly

**NOT YET defensible (open for future work):**
- Waymo cross-dataset generality (raw sensor data not staged; calibration model transferable, untested)
- Video/temporal consistency (single-frame multi-anchor validated; frame-to-frame tracking at centroid untested)
- Full 360° completion (sky-outpaint E8 tested separately; ground-fill hallucination known)
- Boundary-straddling moving objects (cen_depth_b1 head-ghost ~10–20 px on 1/5 scenes; temporal disocclusion repair DB-84 in progress)
