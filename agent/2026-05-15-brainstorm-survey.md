# Brainstorm Survey — Multi-camera AV → 360° Panorama

Date: 2026-05-15
Author: Claude Code, advised by Koi Chen / user
Status: WIP brainstorm + literature survey. Not a final design. Material for `/ultraplan` deep-think.

---

## Part 0 — What is a "360 panorama" in this project?

Three common representations:

| Representation | Shape | Strength | Weakness |
|---|---|---|---|
| Sphere | unit sphere; each point = (θ azimuth, φ elevation) | Physically natural — matches "where you look" | 2D image conversion needs projection |
| Cylinder | side of an upright cylinder; (θ, h) | No pole distortion, good for driving (sky/floor unimportant) | Loses top/bottom |
| **ERP** (equirectangular) | 2:1 rectangle: `x = θ·W/(2π)`, `y = (π/2 − φ)·H/π` | Universal format (YouTube360, Pantheon360, Argus all use it) | Severe pole stretching |

We target **ERP** as the output format because Pantheon360, Argus, and most 360 diffusion models consume ERP.

## Part 0.1 — Why stitching is hard: parallax

Two cameras at different positions see the same 3D point at **different pixel locations**. The displacement (parallax) ∝ 1/distance. Near objects move a lot between views, far objects barely move.

→ Any 2D-only stitching (homography, mesh-warp, OpenCV stitcher default) produces ghosting on near objects.
→ Recovering depth (or full 3D) makes the problem trivially correct: lift each pixel to 3D, then project to whatever surface.

**Almost the entire research progress in panoramic stitching since 2023 has been about going from 2D → 3D-aware.**

---

## Part 1 — Datasets we could use

| Field | **Argoverse 2 Sensor** (recommended) | Waymo Perception | Waymo E2E | nuScenes |
|---|---|---|---|---|
| Cameras | 7 ring + 2 stereo = 9 | 5 | 8 | 6 |
| 360° coverage | Full, ≥25% adjacent overlap | ~230°, rear 130° blind | Full | Full |
| Resolution | 2048 × 1550 | 1920 × 1280 | 1920 × 1280 | 1600 × 900 |
| Frame rate | 20 Hz (LiDAR-synced) | 10 Hz | 10 Hz | 12 Hz |
| Distortion handled | Imagery already undistorted | Undistorted | Undistorted | Undistorted |
| Extrinsics | `egovehicle_SE3_sensor` (quat + t) | Provided | Provided | Provided |
| Download | Public S3, no auth, MIT API | Google login + ToS | Same | Public |
| Size per scenario | ~5–10 GB | ~3–5 GB | Similar | ~3 GB |
| **Existing 360 derivative** | None (open gap) | None | None | **nuScenes-360** (28k+ ERPs, used by Percep360 arXiv 2507.06971) |

**Decision rationale**: AV2 is the strongest single choice — full 360, high res, free, clean API, no existing 360 derivative work. nuScenes-360 is a useful comparison anchor. Waymo's rear blind spot is interesting later as a "diffusion completion" angle.

---

## Part 2 — Method landscape (5 levels)

```
   ┌───────────────────────────────────────────────────────────────────┐
   │  pixel-only 2D            <──────────────────>          full 3D    │
   └───────────────────────────────────────────────────────────────────┘
       │              │              │              │              │
       ▼              ▼              ▼              ▼              ▼
   ┌──────┐      ┌──────┐      ┌──────┐      ┌──────┐      ┌──────┐
   │  L0  │  →   │  L1  │  →   │  L2  │  →   │  L3  │  →   │  L4  │
   │Homo- │      │Cyl/  │      │Depth │      │ 3D    │      │NeRF/ │
   │graphy│      │Sphere│      │aware │      │ found-│      │3DGS  │
   │      │      │+blend│      │      │      │ model │      │      │
   └──────┘      └──────┘      └──────┘      └──────┘      └──────┘
   OpenCV       Industrial    OmniStitch    LiftProj /     CylinderSplat
   Stitcher     360 pipelines ACM MM 2024   PIS3R /        Driving-Gauss
                                            **DVGT 25-12** MagicDrive3D

   + side path: Diffusion polish / completion
   (Argus, Percep360, Generative Panoramic Stitching, 360Anything)
```

### L0 — Homography
- One 3×3 matrix maps image A pixels into image B's frame
- Assumes shared plane → fails on close 3D scenes
- 5-line OpenCV; useful only as smoke test

### L1 — Cylindrical / Spherical projection + classical blending
- Use known calibration to ray-cast each pixel onto a sphere/cylinder
- Composite by feathering or **multi-band blending** (Burt & Adelson 1983: split into low/high-freq pyramids, blend separately, recombine)
- Standard pipeline for consumer 360 cameras (GoPro Fusion, Insta360 etc.)
- **What we plan for week-1 baseline**

### L2 — Depth-aware blending
- Estimate per-image depth, use it to break ties in overlap regions
- **OmniStitch** (ACM MM 2024, [code](https://github.com/tngh5004/Omnistitch)) — SRM (stitching-region maximization) + DAS (depth-aware stitching) modules; trained on CARLA-simulated GV360 dataset; vehicle-agnostic
- Good open-source deep baseline

### L3 — Foundation model 3D-lift + projection
The dominant 2025 paradigm. Use a feed-forward 3D model to convert each image into a dense point map; fuse all views in 3D; project to ERP.

| Model | Family | Output | Code | Notes |
|---|---|---|---|---|
| **DUSt3R** (CVPR 2024) | uncalibrated stereo transformer | scale-free point map per image | ✅ | Pioneer; used by LiftProj |
| **MASt3R** | DUSt3R extension | + image matching | ✅ | Better than DUSt3R for matching |
| **MV-DUSt3R+** (CVPR 2025 oral) | multi-view single-stage | scene point cloud in 2 sec | ✅ | Sparse-view scene reconstruction |
| **VGGT** (CVPR 2025) | regression transformer | point map + pose + depth | ✅ | One of the strongest |
| **Pi3** (our team reproduced) | permutation-equivariant | point map + conf + pose + intrinsic | ✅ | Already runs on Colab A100; outputs cached |
| **DVGT** (arXiv 2512.16919, 2025-12) | driving-specific geometry transformer | **metric-scaled** point map + ego pose | ✅ ([github.com/wzzheng/DVGT](https://github.com/wzzheng/DVGT)) | Trained on Waymo/nuScenes/KITTI/DDAD; beats Pi3 + VGGT on driving; **no Sim(3) alignment needed** |

**Method papers in this slot**:
- **LiftProj** (arXiv 2512.24276, 2025-12) — DUSt3R + equidistant cylindrical projection + MAE-based hole completion. No code released.
- **PIS3R** (arXiv 2508.04236, 2025-08) — VGGT + image-diffusion refinement for large-parallax stitching.
- **Generative Panoramic Image Stitching** (arXiv 2507.07133) — diffusion fine-tune for multi-reference panorama outpainting.

### L4 — Neural rendering (NeRF / Gaussian Splatting)
Reconstruct a continuous 3D scene representation, render any viewpoint.

- **CylinderSplat** (arXiv 2603.05882) — feed-forward panoramic 3DGS with cylindrical triplane; 0.29s/forward, 7 GB; trained on Matterport3D + Replica + 360Loc
- **DrivingGaussian** — composite GS for surrounding dynamic scenes
- **Street Gaussians** — static background + per-object 3DGS, real-time
- **MagicDrive3D** — controllable street scene gen with multi-condition
- **EmerNeRF** — self-supervised spatio-temporal NeRF for driving
- **MARS** — modular NeRF-based driving simulator

These are powerful but expensive. Each scenario requires per-scene optimization (or paid feed-forward inference). **Outside week-1/2 scope.**

### +α — Diffusion polish / blind-spot completion
For Waymo's 230° case or to refine any geometric stitch's boundary artifacts.

- **Argus / Beyond the Frame** (arXiv 2504.07940) — perspective video → 360 video via SVD-based diffusion + view-based alignment + blended decoding. Already in this project tree as `05-argus-video-to-360/`.
- **Percep360** (arXiv 2507.06971) — refines imperfectly-stitched ERPs on nuScenes-360 via local scenes diffusion + probabilistic prompting; produces "Hallucinating 360" outputs.
- **360Anything** (arXiv 2601.16192) — geometry-free perspective→360 lifting via diffusion transformers + circular latent encoding.

---

## Part 3 — Where our existing assets fit

| Asset | What we have | How it plugs in here |
|---|---|---|
| Pi3 reproduction | Pi3X inference on Colab A100, 504×504 input, outputs `(points, conf, pose, intrinsic)`, cache adapter `pi3_to_cache.py` | Drop-in for L3. Replace input with AV2 ring frames. |
| Pantheon360 plan | ERP renderer, geometry placeholder, mini dataflow | Downstream consumer of our ERP outputs. |
| Argus paper | View-based frame alignment, blended decoding logic | Use for Waymo blind-spot completion. |
| ViPE / Koi's PR #16 | Multi-view SLAM init | Alternative pose source if needed. |
| Drive MCP + Colab MCP | Operational pipeline for Drive-based experiments | Reuse for Waymo2Panorama outputs. |

---

## Part 4 — Research narratives that could come out of this

| # | Narrative | Output | Timeline |
|---|---|---|---|
| S1 | **AV2-360**: first publicly released 360° driving dataset | Data contribution + simple-method report | 2–4 weeks |
| S2 | **Pi3-Stitch / DVGT-Stitch**: foundation-model-based panorama stitching | Method paper (parallel to LiftProj, open-source counterpart) | 6–10 weeks |
| S3 | **Driving-360 benchmark**: head-to-head of classical / OmniStitch / PIS3R / DVGT / diffusion | Benchmark paper | 6–10 weeks |
| S4 | **Waymo 230° → 360 completion**: Argus-style diffusion on driving | Application paper | 8+ weeks |
| S5 | **Pantheon360 upstream data engine**: feed our outputs into Pantheon360 training/eval | Engineering contribution + ablation | embedded |
| S6 | **3DGS-based driving panorama**: CylinderSplat on AV2 | Method paper, heaviest | 10+ weeks |

**Recommended combination**: S1 + S2 + S5 (data + method + framing).

---

## Part 5 — Week-by-week plan sketch

### Week 1 (this week): foundation + L1 MVP
- [ ] Scaffold `experiments/Waymo2Panorama/` (this commit)
- [ ] Install `av2` library; download 1 AV2 sensor sequence (~3 GB)
- [ ] Implement spherical projection of 7 ring cams using AV2 extrinsics
- [ ] Implement multi-band blending
- [ ] Output one ~5-second ERP video clip (512×1024)
- [ ] `baseline_diagnosis.md` — document where L1 visibly fails (parallax ghosts, ego-vehicle, exposure mismatch, horizon seams)
- [ ] Pull one nuScenes-360 frame for visual reference
- [ ] Stub L3 module (Pi3/DVGT integration interface; no inference yet)

### Week 2–3: L3 main line
- [ ] Run Pi3 (or DVGT) on each of the 7 AV2 views
- [ ] Sim(3) alignment (Pi3 path) or direct ego-frame (DVGT path)
- [ ] `lift_and_project.py`: confidence-weighted 3D fusion → ERP
- [ ] Quantitative L1 vs L3 comparison (PSNR, parallax-region LPIPS, cycle consistency)
- [ ] Add temporal smoothing (cross-frame pose interpolation)

### Week 4+: L2 baseline + diffusion polish + scale-up
- [ ] OmniStitch baseline run on same AV2 clips
- [ ] LiftProj-style MAE hole completion on uncovered ERP regions
- [ ] Argus-style diffusion polish on seam regions
- [ ] Curate ~10 AV2 sequences → release-ready dataset stub
- [ ] Extend to Waymo (rear blind spot → diffusion completion story)

---

## Part 6 — Engineering details I will handle without bothering you

| Item | Plan |
|---|---|
| AV2 extrinsics | `egovehicle_SE3_sensor` quaternion+translation → 4×4 SE(3) matrices |
| Ego-vehicle occlusion (hood/roof) | Hand-painted ego mask per camera (one PNG each), excluded during fusion |
| Intrinsic distortion | AV2 already provides undistorted; treat as pinhole |
| Pi3 scale-free issue | DVGT alternative (metric-scaled) **or** LiDAR median-depth Sim(3) anchor |
| GPU memory | 504×504 Pi3 inputs × 7 views → fits Colab A100 (verified at Pi3 phase) |
| Temporal smoothness | First pass: per-frame. Second pass: ego-pose-smoothed |
| ERP polar regions | Argus-style height-weighted loss; small weight near poles |
| Evaluation | Cycle-consistency PSNR + visual diagnostics + nuScenes-360 cross-reference |

---

## Part 7 — Open decisions to lock during `/ultraplan` deep-think

1. **L3 backbone**: Pi3 (we own it) vs DVGT (purpose-built for driving, metric, newer)? — recommend **DVGT primary, Pi3 fallback**, but verify code quality first.
2. **Output target**: ERP only, or ERP + 3D cache (for Pantheon360)? — recommend **both**, since 3D cache is already trivially produced by L3.
3. **Paper venue thinking**: data paper (NeurIPS D&B) vs method paper (CVPR/ICCV)? — defer, but having both makes either possible.
4. **License**: code MIT? data follows upstream NC restrictions.
5. **Reproducibility scope**: Colab notebook + scripts? Match Pi3's workflow? — yes, mirror Pi3 pattern.
6. **Stereo cameras**: AV2 has 2 stereo — use them as depth ground truth, or leave for later?
7. **Multi-frame vs single-frame**: pure 7-cam single-shot, or also use temporal context (previous frame's geometry)? — recommend single-shot first.
8. **Self-vehicle removal**: just mask, or actually inpaint?

---

## Part 8 — Sources / further reading

- Argoverse 2: https://argoverse.github.io/user-guide/datasets/sensor.html
- AV2 API code: https://github.com/argoverse/av2-api
- Waymo dataset: https://waymo.com/intl/fil/open/data/perception/
- nuScenes: https://arxiv.org/abs/1903.11027
- Argus: https://arxiv.org/abs/2504.07940
- Pantheon360 author homepage: https://koi953215.github.io/
- LiftProj: https://arxiv.org/abs/2512.24276
- DVGT: https://arxiv.org/html/2512.16919  •  code: https://github.com/wzzheng/DVGT
- OmniStitch: https://github.com/tngh5004/Omnistitch
- PIS3R: https://arxiv.org/abs/2508.04236
- Generative Panoramic Stitching: https://arxiv.org/abs/2507.07133
- 360Anything: https://arxiv.org/abs/2601.16192
- Percep360 / nuScenes-360: https://arxiv.org/html/2507.06971
- CylinderSplat: https://arxiv.org/html/2603.05882
- DrivingGaussian: https://pkuvdig.github.io/DrivingGaussian/
- EmerNeRF: https://emernerf.github.io/
- MagicDrive3D: https://arxiv.org/html/2405.14475
- Survey DUSt3R→VGGT: https://arxiv.org/abs/2507.08448
- MV-DUSt3R+ (CVPR 2025 oral): https://mv-dust3rp.github.io/
- Pi3 paper: https://arxiv.org/abs/2510.* (see `01-pi3/paper/`)
- Burt & Adelson multi-band blending (1983) — foundational reference, no online URL needed
