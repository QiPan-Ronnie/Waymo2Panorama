# Agent Workspace — Waymo2Panorama

> **TL;DR for agents new to this repo**: write to `handoff.md` + `progress.md` + this `README.md` ONLY. Don't create new `.md` files under `deliverables/`. Read the [rules below](#-rules-for-new-agents-per-user-2026-05-27) FIRST.

---

## 🔒 Rules for new agents (per user 2026-05-27)

These rules exist because the repo had bloated to **47 stale finding/summary mds** that duplicated information already in `progress.md`. User asked to consolidate and enforce going forward.

### ✅ DO

| When you... | Do this |
|---|---|
| Finish an experiment | Add a new entry **at the top of `agent/progress.md`** (format: 怎么做 / 结果 / Deliverables / Status / Next) |
| Have visual evidence (PNG/JPG) | Put under `deliverables/<topic>/*.png` — images are NOT bloat, they're evidence |
| Need to hand off to a human (advisor/teammate) | Create `deliverables/handoff_to_<name>_<date>.md` (the one allowed type of new .md in deliverables/) |
| Write paper sections | Put under `paper/` (e.g. `paper/method_draft.md`) |
| Take random research notes | Add to a `progress.md` entry's body, or as a code-module docstring |

### ❌ DON'T

| Don't... | Because... |
|---|---|
| Create `deliverables/*_FINDING.md` / `*_SUMMARY.md` / `*_PIPELINE.md` | Info belongs in `progress.md`; standalone files go stale + bloat |
| Create per-experiment standalone `.md` in `deliverables/` | Same — use a `progress.md` entry |
| Move/rename `handoff.md`, `progress.md`, `README.md` | They're stable entry points; renaming breaks future agents |
| Add new files to `deliverables/archived/` or `notes/archived/` | Those folders are read-only history snapshots |
| Commit user personal files (`self_learning/`, `self reading.md`, `agent/plans/5.26 *`) | They're in `.gitignore` — keep them out |

### Commit hygiene (added 2026-05-27)

- `.gitignore` excludes user personal notes, large data (`.tfrecord`/`.npy`/`.feather`), generated PDFs.
- `.gitattributes` normalizes line endings (`eol=lf` in repo) + declares binaries (PNG/JPG/PDF/tfrecord/npy). No more `LF will be replaced by CRLF` warnings on Windows.
- Direct push to `main` authorized for THIS repo (per `[[feedback-direct-push-main-waymo2pano]]` memory) — no PR review needed.
- Commit messages: imperative subject (≤ 60 chars), then a blank line, then a short body. End with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## The 3 source-of-truth files

- **`handoff.md`** — current-state onboarding doc for any agent picking up this work. **Read this first.** Includes documentation rules (same as here) + recent milestones + infrastructure notes.
- **`progress.md`** — append-only timeline. Each completed track → a 4-line block (怎么做 / 结果 / Deliverables / Status / Next). Latest entry at top.
- **`README.md`** — this file. The agent dir guide. Updated when rules change.

Everything else is historical context (`2026-05-15-brainstorm-survey.md` for the original L0-L4 method landscape) or sub-folders.

---

## Where everything else lives

| Topic | Location |
|---|---|
| Paper drafts (intro/method/experiments/discussion/related_work/outline) | `../paper/` |
| Historical method-landscape brainstorm (5.15) | `2026-05-15-brainstorm-survey.md` (here) |
| Old design plans (N1, parallax fix, etc.) | `plans/` (here, historical) |
| Old design specs | `specs/` (here, historical) |
| External-facing handoffs (delivered to advisor/teammate) | `../deliverables/handoff_to_*.md` |
| User-facing learning doc | `../deliverables/learning_plan.md` |
| User's deep self-study | `../self_learning/` (gitignored, except 6 originally-tracked overview mds) |
| Per-route research notes (archived) | `../notes/archived/` |
| Old experiment-finding mds (archived 2026-05-27) | `../deliverables/archived/` |
| Code | `../code/` |
| Run drivers | `../scripts/` |
| Run outputs (Drive primary) | `../outputs/` (mostly gitignored, `agg_*.json` tracked) |

---

## Latest housekeeping (2026-05-27)

- **47 mds archived** (10 deliverables/*_FINDING/*_SUMMARY → `deliverables/archived/`, 37 notes/*.md → `notes/archived/`).
- **6 paper drafts moved** to `paper/`.
- **3 living docs lock** enforced via the rules above.
- **`.gitignore`** updated for user personal notes + commit hygiene.
- **`.gitattributes`** added for line-ending + binary file declarations.

---

## Active project plan

Active plan v6.1 lives in `~/.claude/plans/snug-shimmying-wave.md` (managed by Claude Code planmode, NOT in this folder).

## Parent project

Sub-project under `koi chen/experiments/Waymo2Panorama/`. Siblings:
- `koi chen/01-pi3/` — Pi3 visual geometry foundation model (we use its outputs)
- `koi chen/04-pantheon360/` — 3D-aware 360° video diffusion (downstream, paused per v6.1 pivot)
- `koi chen/05-argus-video-to-360/` — perspective → 360 video diffusion (blind-spot completion candidate)
- `koi chen/02-vipe/` — ViPE multi-view SLAM (downstream demo for L1 ERP)
