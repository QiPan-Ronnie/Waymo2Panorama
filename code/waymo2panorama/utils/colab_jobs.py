"""
Submit-poll-fetch pattern for long-running Colab cells (W2P-003).

Problem
-------
When a Colab cell runs for several minutes (e.g. Pi3 inference, Phase 2 L3 stitch,
OmniStitch baseline runs), the Claude Code MCP harness can lose its stdio link to
the colab-mcp server even though the server process is still alive. Result: the
agent loses the ability to drive the notebook mid-experiment.

W2P-002 patched the server side (websocket ping_timeout, larger max_size, stale
conn displacement). W2P-003 is the *workflow* fix: never block a single MCP call
for more than ~1 second. Long work runs in a detached subprocess; the agent polls
via short cells.

Usage
-----
```
# Cell A — submit (returns in <1 s)
from waymo2panorama.utils.colab_jobs import start_job
job = start_job(
    cmd=['python', '/content/Waymo2Panorama/scripts/run_l1_baseline.py',
         '--log-dir', LOG_PATH,
         '--out-dir', L1_OUT_DIR,
         '--duration-sec', '5',
         '--save-frames'],
    log_path='/tmp/l1_job.log',
    done_marker=f'{L1_OUT_DIR}/{log_id}/baseline.mp4',
    handle_path='/tmp/l1_job.json',
)
print(f'started PID={job.pid}')

# Cell B — poll status (returns in <1 s; repeat this cell)
from waymo2panorama.utils.colab_jobs import load_job, poll_job, format_status
job = load_job('/tmp/l1_job.json')
print(format_status(poll_job(job)))

# Cell C — display result once state=='done'
```

Each MCP call ends quickly, so the harness sees continual activity but never a
multi-minute blocking call. The agent can interleave other work (writing notes,
preparing Phase 2 scripts, etc.) between polls.

Design notes
------------
- We persist a `Job` handle as JSON next to the log so `load_job(handle_path)`
  recovers it across Python kernel sessions or fresh MCP sessions.
- We `start_new_session=True` so the subprocess survives the parent Python's exit
  (Colab cells can finish without killing the job).
- `done_marker` is the canonical "job complete" signal: a file the script writes
  only on full success. Any expected output path works.
- Stderr is merged into stdout to keep the log path single-file.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Job:
    """In-memory handle for a background job. Round-trips to JSON for persistence."""

    pid: int
    cmd: list
    log_path: str
    done_marker: str
    started_at: float
    handle_path: str


def start_job(
    cmd: list[str],
    log_path: str,
    done_marker: str,
    handle_path: Optional[str] = None,
    env: Optional[dict] = None,
) -> Job:
    """Submit a job in the background and return immediately.

    Args:
        cmd:          subprocess argv list (e.g. ['python', 'script.py', '--arg'])
        log_path:     where stdout+stderr will be written (line-buffered)
        done_marker:  path that the job writes only on completion (used by poll_job)
        handle_path:  where to persist the Job handle JSON (default: log_path + '.job.json')
        env:          optional environment dict (merged with current env)

    Returns:
        Job dataclass with `pid` and metadata. Cell can return this.
    """
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    # Open log in binary write mode so the subprocess can write bytes directly without
    # Python text-mode buffering shenanigans. We separately flush via bufsize=0 on Popen.
    log_fd = open(log_path, "wb", buffering=0)

    full_env = os.environ.copy()
    # PYTHONUNBUFFERED forces the child Python (if any) to flush stdout/stderr per line.
    # Effective only if the subprocess is a Python interpreter; harmless otherwise.
    full_env.setdefault("PYTHONUNBUFFERED", "1")
    if env:
        full_env.update(env)

    proc = subprocess.Popen(
        cmd,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        bufsize=0,                # no Python-level buffering
        start_new_session=True,   # detach from this Python process
        env=full_env,
    )

    handle_path = handle_path or f"{log_path}.job.json"
    job = Job(
        pid=proc.pid,
        cmd=list(cmd),
        log_path=str(log_path),
        done_marker=str(done_marker),
        started_at=time.time(),
        handle_path=str(handle_path),
    )
    Path(handle_path).write_text(json.dumps(asdict(job), indent=2))
    return job


def load_job(handle_path: str) -> Job:
    """Reconstruct a Job from its persisted handle JSON."""
    data = json.loads(Path(handle_path).read_text())
    return Job(**data)


def poll_job(job: Job, tail_bytes: int = 2000) -> dict:
    """Check job status. Returns a dict; never raises.

    Returned dict fields:
        state:         'running' / 'done' / 'crashed' / 'finishing'
        elapsed_s:     seconds since start_job
        marker_exists: whether done_marker file exists
        pid_alive:     whether the subprocess PID is still alive
        log_tail:      last `tail_bytes` bytes of the log (may be empty)
        log_size:      total bytes in the log file
    """
    pid_alive = _is_pid_alive(job.pid)
    marker_exists = os.path.exists(job.done_marker)

    log_tail = ""
    log_size = 0
    if os.path.exists(job.log_path):
        log_size = os.path.getsize(job.log_path)
        try:
            with open(job.log_path, "rb") as f:
                if log_size > tail_bytes:
                    f.seek(-tail_bytes, os.SEEK_END)
                log_tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            pass

    if marker_exists and not pid_alive:
        state = "done"
    elif marker_exists and pid_alive:
        state = "finishing"  # output is written, process still wrapping up (e.g. closing file handles)
    elif not marker_exists and pid_alive:
        state = "running"
    else:  # not marker_exists and not pid_alive
        state = "crashed"

    return {
        "state": state,
        "elapsed_s": time.time() - job.started_at,
        "marker_exists": marker_exists,
        "pid_alive": pid_alive,
        "log_tail": log_tail,
        "log_size": log_size,
        "pid": job.pid,
    }


def wait_for_job(
    job: Job,
    timeout_s: float = 600.0,
    poll_every_s: float = 5.0,
    print_progress: bool = True,
) -> dict:
    """Block until job state in {done, crashed} or timeout. Use only from a long-cell
    context that you accept may block. From an MCP-driven workflow, prefer calling
    poll_job repeatedly from short cells instead.
    """
    deadline = time.time() + timeout_s
    last_print = 0.0
    while time.time() < deadline:
        s = poll_job(job)
        if s["state"] in ("done", "crashed"):
            return s
        now = time.time()
        if print_progress and now - last_print > 10.0:
            print(f"[wait] state={s['state']} elapsed={int(s['elapsed_s'])}s")
            last_print = now
        time.sleep(poll_every_s)
    s = poll_job(job)
    s["timed_out"] = True
    return s


def cancel_job(job: Job) -> bool:
    """Send SIGTERM to the job's process group. Returns True if signal sent successfully."""
    if not _is_pid_alive(job.pid):
        return False
    try:
        if sys.platform == "win32":
            os.kill(job.pid, signal.SIGTERM)
        else:
            os.killpg(os.getpgid(job.pid), signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def format_status(status: dict) -> str:
    """Render a poll_job dict as a human-readable string for printing in cells."""
    state = status["state"]
    elapsed = int(status["elapsed_s"])
    icon = {
        "running":   "⏳",
        "done":      "✅",
        "finishing": "🔄",
        "crashed":   "❌",
    }.get(state, "?")
    lines = [
        f"{icon} state={state}  elapsed={elapsed}s  pid={status['pid']}",
        f"   marker_exists={status['marker_exists']}  pid_alive={status['pid_alive']}  log_size={status['log_size']}B",
    ]
    if status.get("log_tail"):
        lines.append("--- log tail ---")
        lines.append(status["log_tail"].rstrip())
    return "\n".join(lines)


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform 'is this PID still alive?' check."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # On Windows, sending signal 0 isn't supported; use ctypes.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_INFORMATION = 0x0400
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
