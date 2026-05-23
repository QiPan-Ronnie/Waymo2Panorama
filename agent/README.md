# Agent Workspace — Waymo2Panorama

This folder holds **agent-facing** docs for the Waymo2Panorama sub-project (separate from `../deliverables/` which is user-facing / Koi-facing).

## Files (kept lean intentionally)

- **`handoff.md`** — current-state onboarding doc for any agent picking up this work. Read this first.
- **`progress.md`** — append-only single source of truth. Each completed track gets a 4-line entry (怎么做 / 结果 / Deliverables / Status / Next). Latest at top.
- **`2026-05-15-brainstorm-survey.md`** — archive: original method-landscape brainstorm (Argus / LiftProj / OmniStitch / DVGT / CylinderSplat etc.). Concepts still apply but supersedeby actual experiment results in progress.md.

## Where everything else lives (so this folder stays clean)

| Topic | Location |
|---|---|
| Active plan (v6.1) | `C:\Users\14294\.claude\plans\snug-shimmying-wave.md` |
| User-facing learning roadmap | `../deliverables/learning_plan.md` |
| User's deep self-study | `../self_learning/` (5 chapters) |
| Koi-facing final | `../deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.{md, pdf}` |
| Meeting talking points | `../deliverables/meeting_cram.md` |
| Per-route research/design | `../notes/` |
| Code | `../code/` |
| Run drivers | `../scripts/` |
| Run outputs (Drive primary) | `../outputs/` (mostly gitignored, `agg_*.json` tracked) |

## Conventions

- **Append to progress.md** when finishing a track (don't create `progress_T*_addendum.md` — those were cleaned up 2026-05-21).
- **Date-stamped specs** for one-off design docs: `YYYY-MM-DD-<topic>.md`.
- **Active plan** lives in `~/.claude/plans/` (managed by Claude Code planmode), NOT in this folder.

## Parent project

This sub-project sits under `koi chen/experiments/Waymo2Panorama/` in the broader Koi Chen paper-reproduction tree. Siblings:

- `koi chen/01-pi3/` — Pi3 visual geometry foundation model (we use its outputs)
- `koi chen/04-pantheon360/` — 3D-aware 360° video diffusion (downstream, paused per v6.1 pivot)
- `koi chen/05-argus-video-to-360/` — perspective → 360 video diffusion (Argus, blind-spot completion candidate)
- `koi chen/02-vipe/` — ViPE multi-view SLAM (downstream demo for L1 ERP)
