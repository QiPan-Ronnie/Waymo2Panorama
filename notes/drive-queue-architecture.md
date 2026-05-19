# Drive-as-queue architecture (W2P-004)

Date: 2026-05-19
Status: Implemented. See `code/waymo2panorama/utils/drive_queue.py`.

## Problem

The previous robustness fixes (W2P-001 tab binding, W2P-002 ping timeout,
W2P-003 submit-poll pattern) all assume the Claude Code → `colab-mcp` stdio link
stays alive. In practice this link drops after long blocking tool calls and
sometimes after just idle time. Once dropped, the agent loses the ability to
drive the notebook even though both the `colab-mcp` server process and the user's
Colab tab are still healthy.

This is a Claude Code platform limitation, not something `colab-mcp` can fix.

## Solution

Decouple the agent from `colab-mcp` by routing job submission and result retrieval
through services we trust:

| Direction       | Channel                          | Why it's reliable |
|---|---|---|
| Agent → Colab   | `git push` to QiPan-Ronnie/Waymo2Panorama (jobs/*.json) | Already used dozens of times per session; auth via SSH key; survives Claude Code restarts |
| Colab → Agent   | Drive MCP `read_file_content` (results/*.json)          | Drive MCP is an Anthropic-hosted connector (claude.ai → Drive), not local stdio |
| Worker → Drive  | Direct filesystem writes on `/content/drive`            | Colab's own Drive mount, no MCP involved |

## Components

### `code/waymo2panorama/utils/drive_queue.py`
- `DriveQueueWorker` class: Colab-side long loop
  - heartbeat every 5 s
  - git pull every 10 s
  - scan `jobs/*.json` for new specs, claim, spawn subprocess
  - refresh `results/<id>.json` every 3 s with state/log_tail/pid_alive/marker_exists
  - terminal states: `done`, `crashed`, `timeout`
- `submit_job_local(...)`: agent-side helper to write a job spec to the local repo

### `scripts/cell_drive_queue_worker.py`
Stub showing the Cell content. User pastes `WORKER_CELL_CODE` into a new Colab cell
and runs it once per Colab session.

### `jobs/` directory in this repo
- Tracked in git
- Each `<id>.json` is a job spec (cmd, done_marker, timeout, etc)
- README has the schema

## Storage layout

In the repo (committed):
```
jobs/
    README.md
    <id>.json
```

On Drive (NOT in repo):
```
MyDrive/koi_waymo2pano_colab/
    results/
        <id>.json          # current state of each job
    worker/
        heartbeat.json     # touched by worker every 5 s
        stop.flag          # touch to stop the worker cleanly
    outputs/
        l1/, phase2/, ...  # actual experiment outputs (gitignored anyway)
```

## Job lifecycle

1. Agent constructs a spec dict, writes to `jobs/<id>.json` via `submit_job_local()`
2. Agent runs `git add jobs/<id>.json && git commit && git push`
3. Within ~10 s, worker's `git pull` brings the spec in
4. Worker writes `results/<id>.json` with `state=claimed`, spawns subprocess
5. Worker keeps `results/<id>.json` fresh every ~3 s: state, log_tail, pid_alive
6. On final state (done/crashed/timeout), worker stops updating that result
7. Agent reads `results/<id>.json` via Drive MCP whenever convenient

## State machine

```
       create spec      worker pulls      proc starts      proc exits      marker exists
 [none] ───────────▶ [claimed] ───────▶ [running] ──────▶ [crashed]       │
                       │                    │              │              │
                       │                    └──────────────────────────────┴────▶ [done]
                       │                                                 │
                       │                                                 └─────▶ [finishing] ───▶ [done]
                       │
                       └────── timeout ──────▶ [timeout]
```

- `claimed`:   worker accepted the job; subprocess not yet up
- `running`:   PID alive, no marker yet
- `finishing`: marker written, PID still alive (handle closing, etc)
- `done`:      PID exited cleanly, marker exists
- `crashed`:   PID exited without writing marker
- `timeout`:   worker SIGTERM'd the job after `spec.timeout_s`

## Agent-side usage pattern

```python
# 1. Submit
from waymo2panorama.utils.drive_queue import submit_job_local
submit_job_local(
    repo_dir='.',
    job_id='phase2-pi3-frame0',
    cmd=['python', 'scripts/phase2/run_pi3_one_frame.py', '--log-dir', '...'],
    done_marker='/content/drive/MyDrive/.../phase2/pi3_one_frame/summary.json',
    timeout_s=1800,
)
# git add jobs/phase2-pi3-frame0.json && git commit && git push

# 2. Poll (no MCP cell-ops, just Drive read_file_content)
#    -> reads /MyDrive/koi_waymo2pano_colab/results/phase2-pi3-frame0.json
#    -> returns dict with state, elapsed_s, log_tail, marker_exists, ...
```

## Heartbeat / liveness

Agent can check `worker/heartbeat.json` to confirm the worker cell is alive.
If `updated_at` is more than ~60 s ago, worker is dead and we should ask the user
to re-run the worker cell.

## Stopping

`!touch /content/drive/MyDrive/koi_waymo2pano_colab/worker/stop.flag` (in a Colab cell)
or just interrupt the worker cell via Colab UI.

## When to use what

| Workflow                                  | Use this                                |
|---|---|
| Quick agent ↔ cell prototyping (seconds) | `colab-mcp` (with W2P-001..-003)        |
| Long-running experiments (>30 s)         | **Drive queue (W2P-004)**               |
| Multi-hour training / batch eval         | Drive queue, plus check heartbeat       |
| Drive small-file read                    | Drive MCP `read_file_content`           |
| Large file transfer                      | Drive native, then short MCP confirmation |
