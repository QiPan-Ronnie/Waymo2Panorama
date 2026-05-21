# `jobs/` — Drive-queue job specs

This directory is the **inbound queue** for the Drive-as-queue architecture (W2P-004).
The agent writes JSON job specs here and `git push`es. The Colab worker (running the
`DriveQueueWorker` in `code/waymo2panorama/utils/drive_queue.py`) pulls this repo
every ~10 seconds, sees new specs, claims them, and executes them as subprocesses.

Results land on Drive at `MyDrive/koi_waymo2pano_colab/results/<id>.json` (NOT in
this repo). The agent reads results via Drive MCP.

## Job spec schema

Required fields:
- `id`: unique slug (used for filename, results filename, log filename, etc)
- `cmd`: argv list (e.g. `["python", "scripts/run_l1_baseline.py", "--log-dir", "..."]`)
- `done_marker`: path that the command writes only on successful completion;
                 worker uses its existence to detect "done"

Optional fields:
- `log_path`:   where subprocess stdout+stderr go (default `/tmp/drive_queue_logs/<id>.log`)
- `cwd`:        working directory for the subprocess (default `/content/Waymo2Panorama`)
- `env`:        dict of extra env vars
- `timeout_s`:  max runtime in seconds; worker SIGTERMs after this
- `labels`:     dict of free-form string labels. The bootstrap worker
                (Wave 0.5 新-W, `scripts/cell_worker_bootstrap.py`) honours
                `labels.requires` ∈ {`"gpu"`, `"cpu"`, `"any"` (default)} to
                filter jobs by current Colab runtime. CPU-only jobs (HDR
                compensation, graph-cut seam, aggregator, PDF rendering)
                should set `labels.requires = "cpu"` or `"any"`. GPU jobs
                (Pi3 inference, GEN3C, T13 finetune) should set
                `labels.requires = "gpu"`. The worker writes a transient
                `skipped_wrong_runtime` result when runtimes don't match,
                and the job stays in queue for a correct-runtime worker.

Created-at field is auto-added by `submit_job_local()` helper.

## Example

```json
{
  "id": "phase2-pi3-frame0",
  "cmd": ["python", "scripts/phase2/run_pi3_one_frame.py", "--log-dir", "..."],
  "done_marker": "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/summary.json",
  "timeout_s": 1800,
  "labels": {"requires": "gpu"}
}
```

CPU-only example (HDR, graph-cut seam, aggregator, PDF rendering):

```json
{
  "id": "phase3-new-e-hdr-anchor60",
  "cmd": ["python", "scripts/phase3/run_hdr_compensation.py", "--anchor", "60"],
  "done_marker": "/content/drive/.../outputs/phase3/hdr_compensation/done.json",
  "labels": {"requires": "cpu"}
}
```

## Lifecycle

1. Agent: write `jobs/<id>.json` locally, `git add && commit && push`
2. Worker: `git pull` within 10 s, scan jobs, claim by writing `results/<id>.json`
   with `state=claimed`, spawn subprocess
3. Worker: every 3 s update `results/<id>.json` with running/finishing/done state
   plus log_tail, pid_alive, marker_exists, exit_code
4. Worker: on `state=done` or `state=crashed`, stops updating and removes from active set
5. Agent: read `results/<id>.json` via Drive MCP periodically until state terminal

## Why jobs/ stays under git

- Audit trail: every experiment we ran is checked in
- Reproducible: anyone can replay a job by re-pushing the spec
- Diffs: easy to see what changed across runs
- Branchable: parallel agents on different branches don't collide

Result files DON'T go in git — they contain output paths to Drive blobs and would
churn frequently. Drive is the source of truth for results.

## Cleaning up

Done jobs can be moved to `jobs/done/` (just move the file). The worker re-checks
result state on every scan so even a re-pushed spec won't be re-executed if its
result already shows `state=done`.
