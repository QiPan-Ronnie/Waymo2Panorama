# Agent Roster — Slot Definitions & Handoff Templates

Date: 2026-05-16
Purpose: When the user spawns a new Claude Code (or other agent) session to run a parallel track, copy that track's handoff template from here into the new session's first message. Each template is self-contained — gives the new agent its mission, prerequisites, inputs, outputs, branch convention, and review rules.

> See `plan.md` §14 for orchestration overview, `parallel-tracks.md` for per-track mini-plans.

---

## Slot AGT-A-Main

**This is the current session. No handoff needed unless the main is handed off to a fresh agent.**

When handing off Track A to a new agent:

```
You are AGT-A-Main, the lead agent for the Waymo2Panorama project.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\

START BY READING IN THIS ORDER:
1. agent/handoff.md
2. agent/plan.md (currently v2) — §1, §4 phases, §14 parallel tracks
3. agent/2026-05-15-brainstorm-survey.md (method landscape, only re-read if needed)
4. agent/progress.md
5. agent/parallel-tracks.md
6. agent/agent-roster.md (this file)

CURRENT PHASE: <check progress.md>
CURRENT BLOCKER: <check progress.md>

YOUR JOB: Execute the next pending task in the current phase per plan.md. Do NOT skip ahead. Do NOT modify §1-§3 of plan.md without user approval.

WHEN STUCK: Pause, write what you tried to progress.md, ask user.
```

---

## Slot AGT-B-Waymo

**Activation prerequisite**: Track A Phase 2 has produced `code/waymo2panorama/pipeline/lift_and_project.py` committed to main; backbone decision (D1) is locked.

**Handoff template** (paste into new session):

```
You are AGT-B-Waymo, the parallel-track agent for Waymo→360 with diffusion gap-fill.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
BRANCH: parallel/waymo (create from main tag v0.2-l3-mvp)
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\

START BY READING:
1. agent/plan.md §1, §3, §14
2. agent/parallel-tracks.md → "Track B"
3. notes/backbone_decision.md (which backbone Track A locked)
4. ../../../05-argus-video-to-360/ (Argus codebase and paper)
5. Waymo Open Dataset perception docs

YOUR JOB:
- Adapt main track's pipeline to Waymo Perception's 5-cam input (230° coverage, 130° rear blind spot)
- Add ONLY new files: code/waymo2panorama/data_io/waymo_loader.py, code/waymo2panorama/completion/argus_gap_fill.py
- Do NOT modify any existing core module
- Produce outputs/waymo_b/<seg_id>/with_fill.mp4 and notes/waymo_track_report.md
- PR back to main only after Track A Phase 3 ends

GOTCHAS:
- Waymo registration + ToS required; user must arrange access (no agent-side credentials)
- Waymo 5 cams: FRONT, FRONT_LEFT, FRONT_RIGHT, SIDE_LEFT, SIDE_RIGHT — explicitly verify in tutorial_camera_only.ipynb
- Argus expects ERP input; you provide the L3-generated 230° ERP with explicit gap mask
- Don't fall into the "let's also redo L3 for Waymo" trap — reuse what Track A built

WHEN STUCK: write to parallel/waymo/progress.md and message Track A reviewer (user).
```

---

## Slot AGT-C-DVGT

**Activation prerequisite**: Track A Phase 1 ends (tag v0.1-l1-mvp). At least one AV2 log downloaded and verified.

**Handoff template**:

```
You are AGT-C-DVGT, the parallel-track agent for DVGT vs Pi3 standalone evaluation.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
BRANCH: parallel/dvgt-eval (create from main tag v0.1-l1-mvp)
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\

START BY READING:
1. agent/plan.md §5 D1 (the decision your output supports)
2. agent/parallel-tracks.md → "Track C"
3. ../../../01-pi3/ (Pi3 setup, already operational)
4. https://github.com/wzzheng/DVGT and the DVGT paper (arXiv 2512.16919)
5. Track A's downloaded AV2 log location (check data/README.md)

YOUR JOB:
- Run Pi3X on ≥5 AV2 frames (all 7 ring cams each), record outputs
- Clone and run DVGT on same frames
- Build comparison table: point cloud density, scale error vs LiDAR ground truth (use AV2 LiDAR), confidence map utility, GPU mem, latency, edge-case behavior
- Write notes/dvgt_vs_pi3_evaluation.md with clear recommendation (but Track A makes final call)
- Do NOT modify Track A's pipeline integration of either backbone

DELIVERABLES:
- notes/dvgt_vs_pi3_evaluation.md
- outputs/dvgt_pi3_compare/*.ply
- PR back to main as soon as report is solid

GOTCHAS:
- DVGT is 4 days old at time of writing — expect rough edges, undocumented configs
- Pi3 504×504 input; DVGT may want different resolution — note this in report
- Use AV2 LiDAR for scale ground truth, not nuScenes (different ego frame)
```

---

## Slot AGT-D-OmniStitch

**Activation prerequisite**: Track A Phase 2 starts; sequence list locked in `data/README.md`.

**Handoff template**:

```
You are AGT-D-OmniStitch, the parallel-track agent for OmniStitch baseline.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
BRANCH: parallel/omnistitch (create from main tag v0.2-l3-mvp)
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\

START BY READING:
1. agent/parallel-tracks.md → "Track D"
2. https://github.com/tngh5004/Omnistitch (ACM MM 2024)
3. Track A's data/README.md (sequence list)

YOUR JOB:
- Clone OmniStitch into code/external/Omnistitch/
- Configure for AV2 input (resolution, camera count adapter)
- Run on Track A's Phase 2 sequence list
- Outputs at matching resolution to Track A L3 outputs for fair comparison
- Write notes/omnistitch_setup.md with install steps and adaptations

DELIVERABLES:
- outputs/omnistitch/<log_id>/baseline.mp4 per sequence
- notes/omnistitch_setup.md
- PR back to main by Phase 3 Day 5

GOTCHAS:
- OmniStitch trained on CARLA-simulated GV360; sim-to-real gap likely visible
- Their stitching-region-maximization module expects specific input layout; map carefully
- Don't fix their bugs unless trivial; just note them in the setup doc
- Don't modify Track A's core modules
```

---

## Slot AGT-E-LitWatch

**Activation**: continuous, can start anytime.

**Handoff template**:

```
You are AGT-E-LitWatch, the continuous literature watch agent.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
BRANCH: parallel/lit-watch (create from current main, rebase weekly)
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\

START BY READING:
1. agent/parallel-tracks.md → "Track E"
2. agent/2026-05-15-brainstorm-survey.md Part 2 (the method landscape — your reference for "what's already covered")

YOUR JOB (weekly):
- Scan arXiv cs.CV listing for the past 7 days
- Filter for: "panorama" / "360" / "ERP" / "equirectangular" / "stitching" / "multi-view 3D" / "DUSt3R/VGGT/Pi3 driving" / "Argoverse" / "Waymo panorama"
- Score relevance 1-5; entries with score ≥3 get an entry in notes/lit_watch.md
- Entries with score 5 (potential scoop or major method) get a flag at top of progress.md immediately

OUTPUT FORMAT (append-only to notes/lit_watch.md):
## Week of YYYY-MM-DD

### Score 5 (urgent)
- [Title](arxiv-link) — 2-sentence summary. Action: "Track A must read before D8 decision."

### Score 3-4 (monitor)
- [Title](arxiv-link) — 2-sentence summary. Action: "Cite if paper; otherwise file."

### Score 1-2 (ignore)
- bare list of titles for completeness

PR weekly. Don't merge waiting on approval — Track A reviews.

GOTCHAS:
- Don't include random panorama papers (e.g., real-estate VR) — must be relevant to driving / multi-view 3D / stitching method.
- If your weekly entry has 0 findings, write "No relevant work this week." Don't pad.
```

---

## Slot AGT-F-Pantheon

**Activation prerequisite**: Track A Phase 3 ends (tag v0.3-phase3). Stable L3 outputs exist.

**Handoff template**:

```
You are AGT-F-Pantheon, the Pantheon360 integration handoff agent.

REPO: github.com/QiPan-Ronnie/Waymo2Panorama
BRANCH: parallel/pantheon (create from main tag v0.3-phase3)
LOCAL: D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\
RELATED: ../../../04-pantheon360/

START BY READING:
1. agent/parallel-tracks.md → "Track F"
2. ../../../04-pantheon360/scripts/pi3_to_cache.py (existing pattern to mirror)
3. ../../../04-pantheon360/scripts/render_geometry_placeholder.py (existing consumer)
4. ../../../04-pantheon360/agent/pantheon360_reproduction_plan.md (Koi's plan)
5. Track A's outputs/l3/<log_id>/ (your inputs)

YOUR JOB:
- Adapt pi3_to_cache.py into av2_to_cache.py in 04-pantheon360/scripts/ (NEW file there, mirror existing pattern)
- Verify Pantheon360 renderer consumes our cache without error
- Produce end-to-end demo: AV2 sequence → our L3 → Pantheon360 ERP view at multiple yaws
- Write notes/pantheon_integration_report.md

DELIVERABLES:
- 04-pantheon360/scripts/av2_to_cache.py (mirrors pi3_to_cache.py)
- outputs/pantheon_integration/end_to_end_demo.mp4
- notes/pantheon_integration_report.md
- PR back to main (Waymo2Panorama repo) for our notes; coordinate with Koi for 04-pantheon360/ changes

GOTCHAS:
- 04-pantheon360/ is Koi's project. Your changes there should be PR'd separately if you want them merged upstream.
- Pantheon360 cache schema: points, conf_prob, mask, camera_poses, intrinsic, view_yaws, meta — match this.
- conf_prob is sigmoid'd from raw logit (Track A may store raw logit; convert).
```

---

## General handoff rules

1. **Every agent reads `agent/plan.md` and the relevant `parallel-tracks.md` section first.**
2. **Every agent writes daily entries to their track's `progress.md`.** Main track writes to `agent/progress.md`; parallel tracks write to `parallel/<track-name>/progress.md`.
3. **Every commit message includes the track tag**: `[track-b] Add Waymo loader`, `[track-c] DVGT first run report`, etc. Main track commits are unmarked.
4. **No agent unilaterally changes plan.md.** If a discovery invalidates plan.md, write a proposal to `notes/plan-amendment-<topic>.md` and ping the user / Track A.
5. **Merge gate is at main-track Phase 3 end** (or earlier if user explicitly approves). Until then, parallel tracks live on their branches.
6. **Track A is the integration point.** Other tracks deliver findings, not direct main-line edits.
