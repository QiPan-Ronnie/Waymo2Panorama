# Parallel Tracks — Mini-Plans

Date: 2026-05-16
Purpose: Each track that runs in parallel to the **main track (Track A)** has its own mini-plan here. A track is a self-contained workstream that another agent (or this same agent later) can pick up and execute against a defined input/output contract.

> See `plan.md` §14 for the orchestration overview and rules. See `agent-roster.md` for the agent-slot handoff templates.

---

## Track A — Main: AV2 → 360 spine

**Owner**: this Claude Code session (initial)  
**Branch**: `main`  
**Status**: active

**Goal**: Deliver the spine pipeline: AV2 7 ring cams → ERP 360 video, with L1 baseline → L3 (Pi3/DVGT) main line → comparison vs baselines → Pantheon360 integration.

**Phases**: see `plan.md` §4 (Phase 0 → 5).

**Inputs**:
- `01-pi3/` (Pi3 inference, already operational)
- AV2 sensor dataset (downloaded during Phase 0.5)
- Drive + Colab MCP workflow (operational from Pi3 phase)

**Outputs**:
- `code/waymo2panorama/*` (core package)
- `outputs/l1/`, `outputs/l3/`, etc.
- `notes/baseline_diagnosis.md`, `notes/backbone_decision.md`, `notes/paper-angle-decision.md`
- Git tags: `v0.0.5-spike`, `v0.1-l1-mvp`, `v0.2-l3-mvp`, `v0.3-phase3`, `v0.4-pantheon-integration`, `v1.0-phase-completed`

**Reviews**: Track A reviews all parallel-track PRs before merge to main.

---

## Track B — Waymo → 360 with diffusion gap-fill

**Owner**: AGT-B-Waymo (future agent)  
**Branch**: `parallel/waymo`  
**Activation**: when Track A Phase 2 starts (i.e., the L3 main pipeline is alive enough that Track B can reuse `lift_and_project.py`)

**Goal**: Apply the main pipeline to Waymo Perception (5 cams, ~230° coverage, 130° rear blind spot) and integrate Argus / Percep360 / similar diffusion model to **fill the rear blind spot**, producing a Waymo→360 demo.

**Inputs**:
- Track A's `code/waymo2panorama/{projection, blending, pipeline, alignment}/` (read-only, do not modify)
- Track A's L3 backbone choice (Pi3 or DVGT, locked at Phase 2 Day 1-2)
- Waymo Open Dataset (Perception, registration required; Track B agent must arrange dataset access)
- `05-argus-video-to-360/` (Argus model + weights)

**Outputs** (Track B's own, in `parallel/waymo` branch):
- `code/waymo2panorama/data_io/waymo_loader.py` (new file; doesn't modify main)
- `code/waymo2panorama/completion/argus_gap_fill.py` (new submodule)
- `outputs/waymo_b/<seg_id>/with_fill.mp4`
- `notes/waymo_track_report.md`
- Git tag `v0.X-track-b-waymo`

**Non-goals**:
- Do NOT modify any core module (sphere_projection, multiband, lift_and_project, av2_loader)
- Do NOT derive a new L3 method — reuse Pi3 or DVGT as Track A locked it

**Verification**:
- L1 Waymo baseline produces ERP with explicit 130° gap (visible as black wedge)
- L3 Waymo produces ERP with same gap (geometry can't invent unseen pixels)
- Argus fill produces ERP with plausible content in the gap (eyeball gate: rear should match scene context)
- Cross-validation: blind-spot fill doesn't introduce artifacts in observed region

**Merge gate**: PR opens after Track A Phase 3 ends. Track A reviewer must verify no main-module edits.

---

## Track C — DVGT vs Pi3 standalone evaluation

**Owner**: AGT-C-DVGT (future agent)  
**Branch**: `parallel/dvgt-eval`  
**Activation**: Track A Phase 1 ends (so Track A spike has confirmed AV2 data + we have at least L1 outputs to compare against)

**Goal**: Independently evaluate DVGT and Pi3 on identical AV2 frames. Produce a clear quality + cost comparison report that informs Track A's Phase 2 Day 1-2 decision.

**Inputs**:
- Track A's downloaded AV2 log (specific timestamps, ~5 frames)
- Pi3 setup from `01-pi3/`
- DVGT clone from `github.com/wzzheng/DVGT`

**Outputs**:
- `notes/dvgt_vs_pi3_evaluation.md` — comparison table covering: point cloud density, scale metric error vs LiDAR ground truth, confidence map utility, GPU memory, inference latency, edge-case behavior (sun glare, motion blur, transparent surfaces)
- `outputs/dvgt_pi3_compare/*.ply` — side-by-side point clouds
- Git tag `v0.X-track-c-dvgt`

**Non-goals**:
- Do NOT modify Track A's pipeline integration of either backbone
- Do NOT make the decision for Track A — produce data, let Track A decide at Phase 2 Day 1-2

**Verification**:
- Both backbones successfully run on identical input
- Quantitative metrics produced for ≥3 metric columns
- Reviewer (Track A) can read the report and make D1 decision

---

## Track D — OmniStitch baseline

**Owner**: AGT-D-OmniStitch (future agent)  
**Branch**: `parallel/omnistitch`  
**Activation**: Track A Phase 2 starts (so we have a clear sequence list to evaluate against)

**Goal**: Set up OmniStitch ([github.com/tngh5004/Omnistitch](https://github.com/tngh5004/Omnistitch), ACM MM 2024) as the L2 deep baseline; run on Track A's Phase 2 AV2 sequences; produce comparable ERP outputs.

**Inputs**:
- Track A's `data/argoverse2/<log_id>/` (read-only)
- OmniStitch repo + weights (Track D arranges)
- GV360 dataset reference (for sanity checking OmniStitch trained behavior)

**Outputs**:
- `code/external/Omnistitch/` (cloned, configured)
- `outputs/omnistitch/<log_id>/baseline.mp4` per sequence
- `notes/omnistitch_setup.md` — install steps, gotchas, adaptations made for AV2 input
- Git tag `v0.X-track-d-omnistitch`

**Non-goals**:
- Do NOT modify OmniStitch upstream code (use as-is, configure only)
- Do NOT modify Track A's core modules

**Verification**:
- OmniStitch runs on at least 3 AV2 sequences
- Outputs are at same resolution as Track A L3 outputs (for fair comparison)
- Comparison data delivered to Track A by Phase 3 Day 5

---

## Track E — Continuous literature watch

**Owner**: AGT-E-LitWatch (continuous, weekly cadence)  
**Branch**: `parallel/lit-watch`  
**Activation**: anytime (no prerequisites)

**Goal**: Weekly arXiv + venue scan for new work on: multi-camera→panorama stitching, AV-domain panorama generation, 3D foundation models for driving, ERP-aware diffusion models. Flag anything that affects Track A's positioning or method choice.

**Inputs**:
- arXiv cs.CV daily listing
- Google Scholar alerts on key terms
- Specific researcher feeds: Koi Chen, Wei-Chiu Ma (Argus author), DUSt3R / VGGT / Pi3 authors

**Outputs**:
- `notes/lit_watch.md` — append-only weekly log with: paper title, link, 2-sentence summary, relevance score (1-5), action ("monitor" / "ignore" / "discuss with Track A")
- Critical-find slack: if a paper scoops Track A, file an issue/note immediately

**Non-goals**:
- Do NOT produce code or experiments
- Do NOT modify any track's plan unilaterally; flag to Track A who decides

**Verification**:
- One entry per week minimum
- Score-5 findings get same-day flag

---

## Track F — Pantheon360 integration handoff

**Owner**: AGT-F-Pantheon (future agent)  
**Branch**: `parallel/pantheon`  
**Activation**: Track A Phase 3 ends (so we have stable L3 outputs to feed Pantheon360)

**Goal**: Cleanly hand off Track A's L3 outputs into the `04-pantheon360/` pipeline. Adapt `pi3_to_cache.py`, verify Pantheon360 renderer accepts our cache format, demo end-to-end.

**Inputs**:
- Track A's `outputs/l3/<log_id>/` cache outputs
- `04-pantheon360/scripts/pi3_to_cache.py` (existing, may need adaptation)
- `04-pantheon360/scripts/render_geometry_placeholder.py` (existing consumer)

**Outputs**:
- `04-pantheon360/scripts/av2_to_cache.py` (new adapter, mirrors pi3_to_cache.py pattern)
- `outputs/pantheon_integration/end_to_end_demo.mp4`
- `notes/pantheon_integration_report.md`
- Git tag `v0.X-track-f-pantheon`

**Non-goals**:
- Do NOT modify Pantheon360 renderer internals
- Do NOT replace Track A's pipeline — Track F is a downstream consumer

**Verification**:
- Pantheon360 renderer reads our cache without error
- Demo video plays
- Coordinated with Koi (this is his project)

---

## Activation diagram

```
T=0      Phase 0.5 Spike (Track A)
T=1d     Phase 1 starts (Track A: L1)
         └────── Track E (lit-watch) can start anytime, low cost
T=1w     Phase 1 ends → tag v0.1
         └────── Track C activates: DVGT vs Pi3 eval
T=1w+    Phase 2 starts (Track A: L3 backbone D1 decision)
         ├────── Track B activates: Waymo branch starts (reuses L3 once locked)
         └────── Track D activates: OmniStitch setup
T=3w     Phase 2 ends → tag v0.2
T=5w     Phase 3 ends → tag v0.3 + D8 paper angle decision
         ├────── Track B merges back (if ready)
         ├────── Track C merges report (likely earlier)
         ├────── Track D merges baselines
         └────── Track F activates: Pantheon integration handoff
T=7w     Phase 4 ends → tag v0.4
         └────── Track F merges
T=8w     Phase 5: paper / handoff
```
