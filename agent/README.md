# Agent Workspace — Waymo2Panorama

This folder tracks brainstorming, design specs, plans, and progress for the Waymo2Panorama sub-project.

## Files

- `2026-05-15-brainstorm-survey.md` — initial teaching-style brainstorm of the method landscape (Argus, LiftProj, OmniStitch, DVGT, CylinderSplat, etc.) + dataset comparison + recommended path. Input material for deeper planning.
- `handoff.md` — pointer file for the next agent (you).

## Conventions

- Date-stamped specs: `YYYY-MM-DD-<topic>.md`
- Active plan lives in `plan.md` once produced.
- Progress log lives in `progress.md` once work begins.

## Parent project

This sub-project sits under `koi chen/experiments/Waymo2Panorama/` in the broader paper-reproduction tree. Sibling projects:

- `koi chen/01-pi3/` — Pi3 visual geometry foundation model (reproduced, outputs usable here)
- `koi chen/04-pantheon360/` — 3D-aware 360° video diffusion (downstream consumer)
- `koi chen/05-argus-video-to-360/` — perspective → 360 video diffusion (for blind-spot completion)
- `koi chen/02-vipe/` — ViPE multi-view SLAM (alternative pose/intrinsic source)
