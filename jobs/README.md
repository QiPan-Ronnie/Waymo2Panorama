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

---

## ⚠️ Blocked jobs in current queue (as of 2026-05-21, for any agent picking up this project)

There are **3 `phase3-new-f-vggt-*` jobs** in this directory that crashed once and are
intentionally left here pending a user decision. Do NOT re-submit or delete them
without checking with the user first.

### Current state of the 3 jobs

| Job | Status |
|---|---|
| `phase3-new-f-vggt-1-install-v1.json` | **CRASHED** at step 6 (HF ckpt download) with `GatedRepoError 403` |
| `phase3-new-f-vggt-2-eval-v1.json` | guard-skipped (install_done_v1.json missing) |
| `phase3-new-f-vggt-3-tar-cache-v1.json` | guard-skipped (vggt-repo missing) |

### Root cause

`facebook/VGGT-1B-Commercial` is a **gated HuggingFace repo**. Even with a valid
HF token, the user's HF account is not on the access list. The repo's
`model.safetensors` returns 403 on download.

### Retry steps (if user chooses to unblock)

1. **User action (manual, ~30 sec)**: open https://huggingface.co/facebook/VGGT-1B-Commercial
   in a browser, click "**Agree and access repository**". Meta's VGGT is usually
   click-through auto-approve, not human-reviewed.
2. **Verify on Colab**: ensure `HF_TOKEN` is set in the worker env, and that
   `python -c "from huggingface_hub import HfApi; HfApi().model_info('facebook/VGGT-1B-Commercial')"`
   does NOT 403 from inside Colab.
3. **Trigger retry**: nothing in the repo to change — just `git push` an empty commit
   or a no-op tweak to wake the worker. The 3 jobs' `done_marker` files (on Drive
   at `outputs/phase3/p3.5_vggt/install_done_v1.json` etc.) do NOT exist for the
   crashed install, so worker will re-run job 1. Jobs 2 and 3 have install-status
   guards that auto-skip if install fails, so they only run after install succeeds.
4. **Monitor**: heartbeat at `MyDrive/koi_waymo2pano_colab/worker/heartbeat.json`
   (5 s cadence); install_done JSON expected ~15-30 min after worker picks up;
   full eval ~30-60 min after that.

### If user chooses to abandon (alternative)

Delete the 3 job files: `rm jobs/phase3-new-f-vggt-{1,2,3}-*.json` then commit + push.
The 8-route paper is already strong (see `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md`);
VGGT NEG was a paper论据加固 nice-to-have, not a critical contribution.

### Why the result files on Drive show "crashed" / "skipped" but jobs are still here

Worker writes terminal state to `MyDrive/koi_waymo2pano_colab/results/<id>.json` but
does NOT delete the spec in `jobs/`. Specs stay for audit trail. On next pickup,
worker checks the result file — if `state=done` OR a fresh `done_marker` file
exists, worker skips. If the install crashed (no done_marker), worker will retry
on next git pull.

See `agent/handoff.md` "Currently in-flight (Colab worker state)" section for
the operational pointer.
