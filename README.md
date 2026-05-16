# Waymo2Panorama

Reconstruct synchronized 360° panoramic videos from multi-camera autonomous-driving datasets (Argoverse 2, Waymo, nuScenes). Sub-project of the **Koi Chen** paper-reproduction chain; ultimate consumer is **Pantheon360** (CVPR 2026, 3D-aware 360° video diffusion).

## Goal

Take the multi-camera rigs that already exist on AV vehicles (AV2: 7 ring cams, Waymo: 5–8 cams, nuScenes: 6 cams) and produce **clean equirectangular (ERP) panoramas** that are temporally consistent, parallax-aware, and ready to feed Pantheon360 / Argus / world-model pipelines.

## Primary dataset

**Argoverse 2 Sensor** — 7 ring cameras + 2 stereo, full 360° with overlap, MIT-licensed API, undistorted imagery, calibration provided. This is the cleanest entry point.

## Method ladder (planned, simple → complex)

| Level | Method | Status |
|---|---|---|
| L1 | Spherical projection + multi-band blending (classical baseline) | Week 1 target |
| L2 | OmniStitch (ACM MM 2024) — open-source depth-aware deep baseline | Week 3+ |
| L3 | Pi3 / **DVGT** 3D-lift + ERP rasterization (LiftProj-style, our research line) | Week 2–4 |
| L4 | CylinderSplat / Street-Gaussian neural rendering (optional, expensive) | Later |
| +α | Diffusion polish / blind-spot completion (Argus, Percep360) | Later |

## Design notes

See `agent/2026-05-15-brainstorm-survey.md` for the full method-landscape brainstorm with CV-fundamentals teaching.

## License & data

Code: MIT (TBD). Data: respect upstream dataset licenses (AV2: CC-BY-NC, Waymo: research license, nuScenes: CC-BY-NC-SA).
