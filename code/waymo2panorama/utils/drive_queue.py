"""
Drive-as-queue architecture (W2P-004) for robust agent ↔ Colab communication.

Why this exists
---------------
W2P-001/-002/-003 are layered fixes around `colab-mcp`. They help when MCP starts up
cleanly, but they can't recover from "Claude Code harness loses stdio mid-session"
which happens after long cell runs. That fundamentally caps how long an experiment
can run before we need to restart the agent.

This module replaces `colab-mcp` for production-style workflows with a simple
file-based queue:

    ┌────────────┐    git push      ┌───────────┐    git pull (10 s)   ┌────────────┐
    │  Agent     │ ──── jobs/*.json ──▶│  GitHub  │ ─────────────────────▶│  Colab     │
    │  (Claude)  │                  │           │                       │  worker    │
    │            │                  └───────────┘                       │  (long cell)│
    │            │                                                       │            │
    │            │     Drive MCP read    ┌───────────┐    Drive write   │            │
    │            │ ◀──── results/*.json ─│  Drive   │ ◀─────────────────│  subprocess│
    └────────────┘                       └───────────┘                   └────────────┘

What the agent does (no colab-mcp needed):
  1. Construct a JobSpec dict locally
  2. Write it to `jobs/<id>.json` in the repo
  3. `git add && git commit && git push`
  4. Periodically read `results/<id>.json` from Drive via Drive MCP
     (mcp__claude_ai_Google_Drive__read_file_content — KB-level, fast, reliable)

What the user does once per Colab session:
  1. Run cell_drive_queue_worker (see scripts/cell_drive_queue_worker.py)
  2. Leave it running. It loops every few seconds. Can run for hours.

What the worker does:
  - Heartbeats every 5 s to `worker/heartbeat.json` on Drive
  - Git-pulls the repo every 10 s
  - Scans `jobs/*.json`; for each unseen one, claim + spawn subprocess
  - Updates `results/<id>.json` every ~3 s during a job (state, log_tail, etc)
  - Final result has state ∈ {done, crashed} and exit_code

Storage layout
--------------
In the repo (committed):
    jobs/
        README.md
        <id>.json

On Drive (NOT in repo):
    MyDrive/koi_waymo2pano_colab/
        results/<id>.json
        worker/heartbeat.json
        worker/stop.flag        (touched by agent or user to stop the worker)

Why GitHub for incoming + Drive for outgoing?
  - Agent CAN reliably `git push` (we use it dozens of times per session already)
  - Agent canNOT reliably `Drive create_file` (OAuth scope rejections in subdirs)
  - Worker CAN reliably read repo (just `git pull`)
  - Worker CAN reliably write Drive (it's the local /content/drive mount)
  - Agent CAN reliably `Drive read_file_content` (claude.ai connector, hosted by Anthropic)

So we use what each side does well. Worst case the worker dies — heartbeat stops,
agent notices via stale timestamp.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_log_tail(log_path: str, n_bytes: int = 4000) -> tuple[int, str]:
    """Return (log_size_bytes, last_n_bytes_decoded). Never raises."""
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return 0, ""
    try:
        with open(log_path, "rb") as f:
            if size > n_bytes:
                f.seek(-n_bytes, os.SEEK_END)
            data = f.read()
        return size, data.decode("utf-8", errors="replace")
    except OSError:
        return size, ""


class DriveQueueWorker:
    """Colab-side long-running worker. Construct once per Colab session, then `.run()`."""

    def __init__(
        self,
        repo_dir: str = "/content/Waymo2Panorama",
        drive_base: str = "/content/drive/MyDrive/koi_waymo2pano_colab",
        poll_interval_s: float = 5.0,
        pull_interval_s: float = 10.0,
        result_update_s: float = 3.0,
        heartbeat_s: float = 5.0,
        log_dir: str = "/tmp/drive_queue_logs",
    ):
        self.repo_dir = Path(repo_dir)
        self.jobs_dir = self.repo_dir / "jobs"
        self.drive_base = Path(drive_base)
        self.results_dir = self.drive_base / "results"
        self.worker_dir = self.drive_base / "worker"
        self.heartbeat_path = self.worker_dir / "heartbeat.json"
        self.stop_flag = self.worker_dir / "stop.flag"
        self.log_dir = Path(log_dir)

        self.poll_interval_s = poll_interval_s
        self.pull_interval_s = pull_interval_s
        self.result_update_s = result_update_s
        self.heartbeat_s = heartbeat_s

        self.active_jobs: dict[str, dict[str, Any]] = {}

        for d in [self.results_dir, self.worker_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ---- helpers ----

    def _write_result(self, job_id: str, data: dict) -> None:
        path = self.results_dir / f"{job_id}.json"
        path.write_text(json.dumps(data, indent=2))

    def _git_pull(self) -> None:
        try:
            subprocess.run(
                ["git", "-C", str(self.repo_dir), "pull"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:  # noqa: BLE001
            pass

    def _write_heartbeat(self) -> None:
        try:
            self.heartbeat_path.write_text(json.dumps({
                "updated_at": utcnow_iso(),
                "active_jobs": list(self.active_jobs.keys()),
                "poll_interval_s": self.poll_interval_s,
                "pull_interval_s": self.pull_interval_s,
            }))
        except OSError:
            pass

    def _start_job(self, spec: dict) -> dict:
        job_id = spec["id"]
        log_path = spec.get("log_path") or str(self.log_dir / f"{job_id}.log")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_path, "wb", buffering=0)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.update({str(k): str(v) for k, v in spec.get("env", {}).items()})

        cwd = spec.get("cwd") or str(self.repo_dir)

        proc = subprocess.Popen(
            spec["cmd"],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env=env,
            cwd=cwd,
        )
        return {
            "spec": spec,
            "proc": proc,
            "log_path": log_path,
            "started_at": utcnow_iso(),
            "started_t": time.time(),
            "log_fd": log_fd,
        }

    def _refresh_result(self, job_id: str, active: dict) -> str:
        spec = active["spec"]
        proc = active["proc"]
        pid_alive = is_pid_alive(proc.pid)
        marker = spec.get("done_marker")
        marker_exists = bool(marker) and os.path.exists(marker)
        log_size, log_tail = read_log_tail(active["log_path"])

        exit_code = proc.poll()

        if marker_exists and not pid_alive:
            state = "done"
        elif marker_exists and pid_alive:
            state = "finishing"
        elif not marker_exists and pid_alive:
            state = "running"
        else:
            state = "crashed"

        # Honour spec timeout: kill if running too long
        timeout_s = spec.get("timeout_s")
        if state == "running" and timeout_s and (time.time() - active["started_t"]) > float(timeout_s):
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                pass
            state = "timeout"

        self._write_result(job_id, {
            "id": job_id,
            "state": state,
            "started_at": active["started_at"],
            "finished_at": utcnow_iso() if state in ("done", "crashed", "timeout") else None,
            "elapsed_s": time.time() - active["started_t"],
            "pid": proc.pid,
            "pid_alive": pid_alive,
            "marker_exists": marker_exists,
            "log_size": log_size,
            "log_tail": log_tail,
            "exit_code": exit_code,
            "cmd": spec.get("cmd"),
            "done_marker": marker,
        })
        return state

    # ---- main loop ----

    def run(self, verbose: bool = True) -> None:
        last_pull = 0.0
        last_heartbeat = 0.0
        last_result_refresh: dict[str, float] = {}

        if verbose:
            print(f"[worker] start @ {utcnow_iso()}")
            print(f"[worker] repo={self.repo_dir}  drive_base={self.drive_base}")
            print(f"[worker] poll={self.poll_interval_s}s  pull={self.pull_interval_s}s")
            print(f"[worker] stop with: !touch {self.stop_flag}")

        try:
            while not self.stop_flag.exists():
                now = time.time()

                if now - last_heartbeat >= self.heartbeat_s:
                    self._write_heartbeat()
                    last_heartbeat = now

                if now - last_pull >= self.pull_interval_s:
                    self._git_pull()
                    last_pull = now

                # Scan job specs
                if self.jobs_dir.exists():
                    for job_file in sorted(self.jobs_dir.glob("*.json")):
                        try:
                            spec = json.loads(job_file.read_text())
                            job_id = spec.get("id")
                        except (OSError, json.JSONDecodeError):
                            continue
                        if not job_id or job_id in self.active_jobs:
                            continue

                        res_path = self.results_dir / f"{job_id}.json"
                        if res_path.exists():
                            try:
                                prior = json.loads(res_path.read_text())
                                if prior.get("state") in ("done", "crashed", "timeout"):
                                    continue
                            except (OSError, json.JSONDecodeError):
                                pass

                        if verbose:
                            print(f"[worker] claiming {job_id}")
                        self._write_result(job_id, {
                            "id": job_id,
                            "state": "claimed",
                            "claimed_at": utcnow_iso(),
                            "cmd": spec.get("cmd"),
                        })
                        try:
                            self.active_jobs[job_id] = self._start_job(spec)
                            if verbose:
                                print(f"[worker]   pid={self.active_jobs[job_id]['proc'].pid}")
                        except Exception as e:  # noqa: BLE001
                            if verbose:
                                print(f"[worker]   start failed: {e}")
                            self._write_result(job_id, {
                                "id": job_id,
                                "state": "crashed",
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "finished_at": utcnow_iso(),
                            })

                # Refresh result files for active jobs
                for job_id in list(self.active_jobs.keys()):
                    if now - last_result_refresh.get(job_id, 0) < self.result_update_s:
                        continue
                    active = self.active_jobs[job_id]
                    state = self._refresh_result(job_id, active)
                    last_result_refresh[job_id] = now
                    if state in ("done", "crashed", "timeout"):
                        if verbose:
                            print(f"[worker] {job_id} -> {state}  (exit={active['proc'].poll()})")
                        try:
                            active["log_fd"].close()
                        except OSError:
                            pass
                        del self.active_jobs[job_id]
                        last_result_refresh.pop(job_id, None)

                time.sleep(self.poll_interval_s)

            if verbose:
                print(f"[worker] stop flag at {self.stop_flag}, exiting at {utcnow_iso()}")
        except KeyboardInterrupt:
            if verbose:
                print(f"[worker] KeyboardInterrupt at {utcnow_iso()}")
        finally:
            for active in self.active_jobs.values():
                try:
                    active["log_fd"].close()
                except OSError:
                    pass


def submit_job_local(
    repo_dir: str,
    job_id: str,
    cmd: list,
    done_marker: str,
    log_path: str | None = None,
    cwd: str | None = None,
    env: dict | None = None,
    timeout_s: int | None = None,
) -> Path:
    """Helper for agent-side: write a job spec JSON to <repo_dir>/jobs/<id>.json.

    The agent should then `git add && commit && push` so the Colab worker can pull it.
    """
    spec = {
        "id": job_id,
        "created_at": utcnow_iso(),
        "cmd": cmd,
        "done_marker": done_marker,
    }
    if log_path:
        spec["log_path"] = log_path
    if cwd:
        spec["cwd"] = cwd
    if env:
        spec["env"] = env
    if timeout_s:
        spec["timeout_s"] = int(timeout_s)

    jobs_dir = Path(repo_dir) / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / f"{job_id}.json"
    path.write_text(json.dumps(spec, indent=2))
    return path
