# Agent Workspace — Waymo2Panorama

This folder holds **agent-facing** docs for the Waymo2Panorama sub-project. **Kept intentionally lean** — only 3 living docs:

## The 3 source-of-truth files (read these, write to these)

- **`handoff.md`** — current-state onboarding doc for any agent picking up this work. **Read this first.**
- **`progress.md`** — append-only timeline. Each completed track → a 4-line block (怎么做 / 结果 / Deliverables / Status / Next). Latest entry at top.
- **`README.md`** — this file. The agent dir guide.

Everything else is historical context (`2026-05-15-brainstorm-survey.md` for the original L0-L4 method landscape) or sub-folders.

## Rules for new agents (per user 2026-05-27)

✅ **DO**:
- Add experiment results to `progress.md` as a new entry at the top
- Put PNG/visual evidence under `../deliverables/<topic>/*.png`
- For formal handoffs to humans (advisor/teammate), create `../deliverables/handoff_to_<name>_<date>.md`
- Put paper drafts under `../paper/`

❌ **DON'T**:
- Don't create `deliverables/*_FINDING.md` / `*_SUMMARY.md` / `*_PIPELINE.md` (info belongs in `progress.md` — those files become stale and bloat the repo)
- Don't create per-experiment standalone docs in `deliverables/` — use `progress.md` entries instead
- Don't move/rename `handoff.md` or `progress.md` — they're stable entry points

## Where everything else lives

| Topic | Location |
|---|---|
| Paper drafts (intro/method/experiments/discussion/related_work/outline) | `../paper/` |
| Historical method-landscape brainstorm (5.15) | `2026-05-15-brainstorm-survey.md` (here) |
| Old design plans (N1, parallax fix, etc.) | `plans/` (here, historical) |
| Old design specs | `specs/` (here, historical) |
| External-facing handoffs (delivered to advisor/teammate) | `../deliverables/handoff_to_*.md` |
| User-facing learning doc | `../deliverables/learning_plan.md` |
| User's deep self-study | `../self_learning/` (5 chapters) |
| Per-route research notes (archived) | `../notes/archived/` |
| Old experiment-finding mds (archived 2026-05-27) | `../deliverables/archived/` |
| Code | `../code/` |
| Run drivers | `../scripts/` |
| Run outputs (Drive primary) | `../outputs/` (mostly gitignored, `agg_*.json` tracked) |

## Active project plan

Active plan v6.1 lives in `~/.claude/plans/snug-shimmying-wave.md` (managed by Claude Code planmode, NOT in this folder).

## Parent project

Sub-project under `koi chen/experiments/Waymo2Panorama/`. Siblings:
- `koi chen/01-pi3/` — Pi3 visual geometry foundation model (we use its outputs)
- `koi chen/04-pantheon360/` — 3D-aware 360° video diffusion (downstream, paused per v6.1 pivot)
- `koi chen/05-argus-video-to-360/` — perspective → 360 video diffusion (blind-spot completion candidate)
- `koi chen/02-vipe/` — ViPE multi-view SLAM (downstream demo for L1 ERP)
