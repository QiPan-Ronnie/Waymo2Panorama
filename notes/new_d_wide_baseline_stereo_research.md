# 新-D Wide-Baseline Stereo — Research Report

**Explore agent**: a925d7e (2026-05-21)
**Recommendation**: **kornia LightGlue + DLT triangulation** primary (GPU-native, lightweight); pycolmap as robust fallback

---

## §1 Stack Choice

| Aspect | kornia LightGlue + DLT (primary) | pycolmap (fallback) |
|---|---|---|
| Install | `pip install kornia torch opencv-python` | `pip install pycolmap` |
| GPU | ✅ Native PyTorch | ❌ CPU-bound C++ |
| 2-view API | Direct: `LightGlueMatcher` + `kornia.geometry.linear_triangulation()` | `pycolmap.twoway_triangulation()` overkill for known T_a_b |
| Time/pair | 100-300 ms GPU, 500 ms - 2 s CPU | 1-5 s |
| Match density | 50-500 inliers/pair typical | Similar |
| Failure mode | Poor matches on repetitive features (roads, vehicles) | Same; slower post-processing |

**Why kornia LightGlue**: 2-view stereo with KNOWN T_a_b is exact (no SfM needed). LightGlue (ICCV 2023) is modern + GPU-native + handles wide baselines via adaptive NN selection. SuperPoint > SIFT > ORB for accuracy on automotive (SuperPoint 0.34% vs ORB 4.15% trans. error on KITTI).

---

## §2 AV2 Calibration

AV2 extrinsics are **factory-locked** (not online-adaptive) and stable:
- Format: quaternion (qw, qx, qy, qz) + translation (tx_m, ty_m, tz_m), in meters
- Accuracy: typical automotive ±5-10 mm baseline, ±0.1° rotation

**Key consequence**: T_a_b = inv(T_ego_a) @ T_ego_b is KNOWN exactly → **no pose estimation, only epipolar filtering + triangulation**.

AV2 ring cam baselines (~2 m platform):
- front_center ↔ front_left/right: ~0.3-0.5 m
- side ↔ rear: ~1.0-1.5 m
- front ↔ side: ~1.5-2.0 m

Disparity search range: ~50-200 pixels at 1024×2048 ERP, manageable.

---

## §3 Pipeline (5 steps)

```python
# scripts/phase3/run_wide_baseline_stereo.py
device = torch.device('cuda')
extractor = kornia.feature.DISK(max_num_keypoints=2048).eval().to(device)
matcher = kornia.feature.LightGlueMatcher().to(device)

for cam_a, cam_b in ADJACENT_PAIRS:
    # 1. Feature detect (DISK) + descriptor
    kpts_a, desc_a = extractor(grayscale(img_a))
    kpts_b, desc_b = extractor(grayscale(img_b))
    
    # 2. Match (LightGlue)
    matches = matcher({"image0": img_a, "image1": img_b},
                      {"keypoints0": kpts_a, "keypoints1": kpts_b,
                       "descriptors0": desc_a, "descriptors1": desc_b})
    mkpts_a = kpts_a[matches[:, 0]]
    mkpts_b = kpts_b[matches[:, 1]]
    
    # 3. Compute F from KNOWN T_a_b (no estimation needed)
    R, t = T_a_b[:3, :3], T_a_b[:3, 3]
    E = skew(t) @ R
    F = inv(K_b).T @ E @ inv(K_a)
    
    # 4. RANSAC epipolar filter (cv2.findFundamentalMat with USE_FM_RANSAC, threshold 2.0)
    inlier_mask = epipolar_filter(mkpts_a, mkpts_b, F)
    
    # 5. Triangulate (cv2.triangulatePoints DLT)
    P_a = K_a @ [I | 0]
    P_b = K_b @ [R | t]
    pts_3d_cam_a = triangulate(P_a, P_b, mkpts_a[inlier_mask], mkpts_b[inlier_mask])
    pts_3d_ego = T_ego_cam_a @ pts_3d_cam_a   # back to ego frame
    
    save(f"stereo_{anchor}_{cam_a}__{cam_b}.npz", pts_3d_ego=pts_3d_ego, ...)
```

---

## §4 Cam-Pair Adjacency + Expected Overlap

Using RING_CAMS_7 from av2_loader.py:

| Pair | Baseline (m) | Expected Azimuth Overlap | Notes |
|---|---|---|---|
| front_center ↔ front_left | 0.3-0.5 | ~15-25° | Good; straight-ahead |
| front_left ↔ side_left | 1.0-1.2 | ~10-15° | Moderate; side distortion |
| side_left ↔ rear_left | 1.0-1.5 | ~5-10° | Tight; rear boundary |
| rear_left ↔ rear_right | 0.5-1.0 | ~20-30° | Good; rear stereo |
| rear_right ↔ side_right | 1.0-1.5 | ~5-10° | Tight |
| side_right ↔ front_right | 1.0-1.2 | ~10-15° | Moderate |
| front_right ↔ front_center | 0.3-0.5 | ~15-25° | Good |

---

## §5 Integration with L1 — Option B (weighted blending)

**Recommended (Option B)**: Use sparse stereo 3D points as **confidence mask** for L1's existing blend weights — don't change geometry, just reweight.

```python
for cam_a, cam_b in adjacent_pairs:
    sparse_pts_3d = load_stereo_points(cam_a, cam_b)
    # For pixels in overlap with stereo evidence:
    # If stereo depth disagrees with L1's infinity assumption (depth < 50 m):
    #   - Reduce L1 weight at those overlap pixels for both cams
    #   - Pick the cam whose ray is closer to the stereo point as winner
```

**Option A** (replace L1 pixels with stereo-depth-corrected pixels): too aggressive, sparse stereo can't replace dense.
**Option C** (mask out pixels where stereo contradicts L1): aggressive, may create visible holes. Use only if B doesn't improve.

---

## §6 Expected Outcome (Honest)

**If sparse stereo succeeds (clean matches, stable triangulation)**:
- 100-500 depth samples per adjacent pair
- Reweight L1 blends in overlap region
- ΔPSNR: **+0.05 to +0.3 dB** (sparse coverage limits upside)

**High-risk failure modes**:
1. Repetitive structures (roads, building facades) → ambiguous matches → RANSAC rejects majority
2. Lens distortion delta between fisheye/pinhole pairs → broken epipolar constraint
3. Distant features (>50 m) → small disparity → triangulation noise dominates
4. Sparse only (100 pts) can't fix 1024×2048 ERP without densification → densification adds 1-2 days

**Realistic 4-day plan (matches v6.1 estimate)**:
- D1: kornia LightGlue + epipolar RANSAC pipeline (existing cv2 tools)
- D2: Triangulation + ego-frame projection + per-pair depth export
- D3: Integration test with L1 (Option B reweighting); visual on 3-5 frames
- D4: 10-anchor metric eval; pivot to Option C masking if Δ < 0 dB

---

## §7 Install (Colab + Windows)

```bash
pip install kornia torch opencv-python numpy scipy pillow pandas
# Optional fallback:
pip install pycolmap
```

Kornia LightGlue auto-downloads weights on first use. ~5 min install on Colab.

---

## §8 Critical Files for gp Implementer

**Read first**:
- `code/waymo2panorama/data_io/av2_loader.py` (RING_CAMS_7 order, calib loading)
- `code/waymo2panorama/projection/sphere_projection.py` (ERP geometry for overlap)
- `outputs/phase3/pi3_cache/anchor_<id>/image_<cam>.png` (input RGB)

**Outputs (gp must produce)**:
- `code/waymo2panorama/stereo/wide_baseline_stereo.py` (~250 lines)
- `scripts/phase3/run_wide_baseline_stereo.py` (driver)
- `outputs/phase3/p3.6_stereo/anchor_<id>/stereo_<cam_a>__<cam_b>.npz` per pair
- `deliverables/images/route_wide_baseline_depth.png` (5 cam-pair depth viz mosaic)
- `deliverables/handoff_to_koi_v6.md` route 13 section

**Mitigations to bake in**:
- Verify lens distortion → undistort pre-process if needed
- Start sparse-only (no densification) to validate geometry
- Test on 3-5 anchors first; assess match density before scaling
- If < 50 inliers/pair → switch to Option C masking

---

## Sources

- [Kornia LightGlue Tutorial](https://www.kornia.org/tutorials/nbs/image_matching_lightglue.html)
- [LightGlue paper (ICCV 2023)](https://arxiv.org/pdf/2306.13643)
- [PyCOLMAP docs](https://colmap.github.io/pycolmap/pycolmap.html)
- [AV2 sensor guide](https://argoverse.github.io/user-guide/datasets/sensor.html)
