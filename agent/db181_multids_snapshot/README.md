# db181_multids — rescue snapshot (2026-08-16)

Verbatim copy of `agent/db181_multids/` from the worktree at
`experiments/Waymo2Panorama/.worktrees/db213-root-artifact-fixes`.

**Why this exists:** 20 of these files were in `??` (untracked) state in that
worktree, so they lived in no commit anywhere — including
`nuscenes_strict_sync.py`, which is the dependency for the cheapest DB-241 port
(nuScenes). That worktree's `.git` is owned by another Windows account
(`QiPan/CodexSandboxOffline`); it was read once with a one-shot
`-c safe.directory` and **not modified** — global git config was not touched.

This is a snapshot for durability, not a fork. If the original worktree gets
committed upstream, prefer that copy and delete this one.

Adapters present: `nuscenes_adapter.py`, `waymo_perception_adapter.py`,
`waymo_e2e_adapter.py`, `pandaset_adapter.py`, plus `nuscenes_strict_sync.py`,
the scene-band runner/policy/gate, and the v1–v8 source-builder lineage.
