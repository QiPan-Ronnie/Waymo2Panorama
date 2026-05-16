# Waymo2Panorama Agent Handoff

Updated: 2026-05-15

## TL;DR

This sub-project produces 360° panoramic videos (ERP) from autonomous-driving multi-camera rigs (primarily Argoverse 2). Downstream consumer is Pantheon360. Method plan: classical L1 baseline → Pi3/DVGT-based 3D-lift (L3) → optional diffusion polish.

## Start here

1. Read `2026-05-15-brainstorm-survey.md` — full survey of available methods, dataset comparison, CV concepts, recommended path.
2. Check parent project state in `../../../01-pi3/agent/pi3_handoff.md` — Pi3 inference is operational; outputs can be reused.
3. Check Pantheon360 plan in `../../../agent/pantheon360_reproduction_plan.md` — downstream consumer.

## Project chain context (Koi Chen paper line)

```
AV2 / Waymo / nuScenes multi-cam (this project)
        │
        ▼ stitch / 3D-lift / render
ERP panoramic video + 3D cache
        │
        ▼
04-pantheon360  (3D-aware 360° video diffusion, CVPR 2026)
        │
        ▼
360 world simulation, Cosmos-Predict, etc.
```

## Status

- 2026-05-15: Sub-project scaffold created. Brainstorm survey written. Awaiting design approval + plan via `/ultraplan` or `/writing-plans`.
- No code, no data downloads, no runs yet.

## Open decisions

- [ ] Final dataset commitment (AV2 main + nuScenes ref + Waymo later? approved in brainstorm message but not yet locked in spec)
- [ ] L3 backbone: **Pi3** (we already reproduced) vs **DVGT** (driving-specific, metric-scaled, just released 2025-12)
- [ ] Whether to commit the eventual paper-worthy method angle to "Pi3-Stitch" (general geometry model) vs "Driving-Stitch" (specialized)

## Tooling

- Python 3.10/3.12 (Colab compat)
- AV2 API: `pip install av2`
- Pi3: see `../../../01-pi3/`
- Compute: Colab A100 (matches existing Pi3 workflow)
