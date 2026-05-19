# Migration to `agent-colab-queue` (W2P-005)

Date: 2026-05-19
Status: in progress

## What changed

The Drive-as-queue infrastructure that was inline in `code/waymo2panorama/utils/drive_queue.py` has been **extracted** into a reusable standalone package at
[github.com/QiPan-Ronnie/agent-colab-queue](https://github.com/QiPan-Ronnie/agent-colab-queue),
with an **MCP server** wrapping it for native agent access.

Waymo2Panorama is the first consumer of the new package.

## What stays / what's gone

| Asset | Status |
|---|---|
| `code/waymo2panorama/utils/drive_queue.py` | **kept** for backward compat (older cell still works). Will deprecate at v0.3-tag. |
| `scripts/cell_drive_queue_worker.py` | **kept** (W2P-004 worker, points to local module) |
| `scripts/cell_acq_worker.py` | **new** — uses pip-installed agent_colab_queue package |
| `jobs/` directory | unchanged (same on-disk format) |
| Drive `results/` and `worker/` layout | unchanged |
| Old worker's in-flight jobs | transparently picked up by new worker (same dir/format) |

## How the user migrates

1. **Don't start two workers at once.** They race on the same `jobs/` dir.
2. Stop the W2P-004 worker first: interrupt that cell, OR `!touch /content/drive/MyDrive/koi_waymo2pano_colab/worker/stop.flag` from another cell.
3. Wait for the old worker to exit (a few seconds).
4. Delete the stop.flag: `!rm /content/drive/MyDrive/koi_waymo2pano_colab/worker/stop.flag`.
5. Paste the new cell content from `scripts/cell_acq_worker.py::CELL_CODE` into a new cell.
6. Run it. The new worker will `pip install agent-colab-queue` (one-time), then start its loop.
7. Old W2P-004 cell can be deleted from the notebook now.

## What the agent gains

Once `~/.claude.json` is updated to load the new MCP server (Phase D), the agent has direct tools:

| Old way | New way |
|---|---|
| Manually craft `jobs/<id>.json`, `git add`, `git commit`, `git push` via Bash | `mcp__acq__submit_job(workspace="waymo2panorama", job_id="...", cmd=[...], done_marker="...")` |
| `mcp__claude_ai_Google_Drive__search_files` then `download_file_content` for results | unchanged — still uses Drive MCP for results (by design) |
| Hand-compute Drive search query | `mcp__acq__workspace_info("waymo2panorama")` returns search hints |

## Verification gate

Migration is "complete" when:
- [x] `agent-colab-queue` v0.1.0 pushed to GitHub
- [ ] new worker cell runs end-to-end on Colab and writes a heartbeat to Drive
- [ ] agent issues `mcp__acq__submit_job` from a Claude Code session and the worker picks it up
- [ ] result JSON appears on Drive with `state=done` within expected timeframe

## Rollback plan

If anything goes wrong: re-paste the old W2P-004 cell content from `scripts/cell_drive_queue_worker.py::WORKER_CELL_CODE`. The on-disk format is identical, so it'll resume any in-flight job seamlessly.
