# Waymo2Panorama Progress

## Active state

- Phase 0 (repo bootstrap) — **COMPLETE** (commit `4d534f2`)
- Plan v0 → v1 synthesis with ultraplan — **COMPLETE** (commit `5b571e3`)
- Plan v1 → v2 (parallel tracks + Phase 0.5 spike) — **COMPLETE** (commit `6da51eb`)
- Phase 0.5 scaffold (pyproject, scripts, package) — **COMPLETE** (commit `fb05a0a`)
- Phase 0.5 Colab notebook (10 cells, via colab-mcp) — **COMPLETE**
- Phase 0.5 Spike execution — **GO** ✅ (2026-05-16, see `notes/spike-report.md`)
- Phase 1 L1 baseline code — **STARTING**

## Phase 0.5 Spike — GO summary

| Check | Result |
|---|---|
| av2 importable | PASS |
| Dataloader class found | PASS (2 paths work) |
| 7 ring cam dirs present | PASS (319 frames each @ 20 Hz) |
| Mosaic produced | PASS (7 cams + 1 empty placeholder, surrounding view coherent) |
| Sync delta < 50 ms | PASS (**22.49 ms** actual) |
| Calibration readable | PASS (intrinsics + extrinsics feather columns identified) |

**Pinned log**: `02a00399-3857-444e-8db3-a8f58489c394` (val split, ~5-10 GB, 15.90 s of 20 Hz driving)

**Critical finding for Phase 1**: `ring_front_center` is **portrait (2048×1550)**; the other 6 ring cams are **landscape (1550×2048)**. Sphere projection code must read per-camera image shape, not hard-code.

## Track status

| Track | Status | Branch | Next |
|---|---|---|---|
| **A — Main (AV2 spine)** | active, **Phase 1 starting** | `main` | Write `av2_loader.py` |
| B — Waymo + diffusion fill | not activated | `parallel/waymo` | activates at Phase 2 start |
| C — DVGT vs Pi3 eval | not activated | `parallel/dvgt-eval` | activates at Phase 1 end |
| D — OmniStitch baseline | not activated | `parallel/omnistitch` | activates at Phase 2 start |
| E — Lit watch | available anytime | `parallel/lit-watch` | user spawns when desired |
| F — Pantheon integration | not activated | `parallel/pantheon` | activates at Phase 3 end |

## Known issues (active)

| ID | Issue | Status |
|---|---|---|
| W2P-001 | `colab-mcp` `open_colab_browser_connection` re-binds to a fresh empty notebook instead of user's focused tab. Workaround: paste outputs (screenshots) instead of relying on MCP `get_cells` after re-binding. | Under investigation (background agent) |

## Update log

| Date | Update |
|---|---|
| 2026-05-16 | Phase 0.5 Spike **GO**. Ran 10 cells in Colab via MCP up to Cell 6; user pasted Cell 7 screenshot. All checks PASS, sync 22.49ms, 319 frames × 7 ring cams + 2 stereo. Wrote `notes/spike-report.md`. Logged W2P-001 colab-mcp bug; investigation in progress. Phase 1 unblocked. |
| 2026-05-16 | Plan v2: Waymo → Track B, Phase 0.5 inserted, D1/D8 deferred decisions, §14 parallel-tracks. `agent/parallel-tracks.md` + `agent/agent-roster.md` written. |
| 2026-05-15 | Repo created at github.com/QiPan-Ronnie/Waymo2Panorama. Brainstorm survey + plan v0 + plan v1 (ultraplan synthesis) committed. |
