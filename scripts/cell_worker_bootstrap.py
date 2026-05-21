"""scripts/cell_worker_bootstrap.py — Wave 0.5 新-W bootstrap.

One-line Colab cell (replaces older cell_acq_worker.py CELL_CODE):

    from google.colab import drive; drive.mount('/content/drive', force_remount=False)
    !git clone --depth 1 https://github.com/QiPan-Ronnie/Waymo2Panorama /content/Waymo2Panorama 2>/dev/null; cd /content/Waymo2Panorama && git pull -q && python scripts/cell_worker_bootstrap.py

Idempotent: safe to re-run after Colab disconnect or CPU↔GPU runtime switch.

Does the following:
  1. Detect Colab runtime (CPU / T4 / V100 / L4 / A100)
  2. Verify Drive is mounted
  3. Git-pull the Waymo2Panorama repo
  4. If GPU runtime AND Drive cache restore.sh exists → restore conda env
     (~5 min instead of 60 min fresh install)
  5. pip install agent-colab-queue if missing
  6. Start a background heartbeat thread (writes to Drive every 30 s as
     fallback in case the worker thread itself dies)
  7. Start RuntimeAwareWorker poll loop (honours labels.requires in jobs)
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_URL = "https://github.com/QiPan-Ronnie/Waymo2Panorama"
REPO_DIR = Path("/content/Waymo2Panorama")
DRIVE_BASE = Path("/content/drive/MyDrive/koi_waymo2pano_colab")
RESTORE_SH = DRIVE_BASE / "cache" / "t11_gen3c" / "restore.sh"
LOCK_FILE = Path("/tmp/w2p_bootstrap.lock")
BOOTSTRAP_HEARTBEAT = DRIVE_BASE / "worker" / "heartbeat_bootstrap.json"
BOOT_LOG_DIR = DRIVE_BASE / "worker" / "boot_logs"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _acquire_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[boot] another bootstrap is already running on this Colab instance; aborting.")
        sys.exit(0)
    fd.write(str(os.getpid()))
    fd.flush()
    return fd  # caller must keep ref so lock holds for process lifetime


def detect_runtime() -> tuple[str, str]:
    """Return (kind, friendly_name) where kind ∈ {gpu, cpu}."""
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            for tag in ("A100", "L4", "V100", "T4", "H100", "L40S"):
                if tag in name:
                    return "gpu", tag
            return "gpu", name
    except Exception:
        pass
    return "cpu", "cpu"


def ensure_drive():
    """Verify Drive is mounted. Retry 6 × 5 s."""
    for _ in range(6):
        if DRIVE_BASE.exists():
            return
        time.sleep(5)
    print(f"[boot] FATAL: {DRIVE_BASE} not found. Mount Drive via "
          "`from google.colab import drive; drive.mount('/content/drive')` and re-run.")
    sys.exit(2)


def ensure_repo():
    """Ensure /content/Waymo2Panorama exists and is up to date."""
    if not (REPO_DIR / ".git").exists():
        print(f"[boot] cloning {REPO_URL} ...")
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "pull", "--rebase", "-q"],
                   check=False, timeout=60)


def restore_gpu_env(runtime_kind: str):
    """If GPU + restore.sh exists, run it (untar conda env ~5 min)."""
    if runtime_kind != "gpu":
        print("[boot] CPU runtime — skipping GPU env restore.")
        return
    if not RESTORE_SH.exists():
        print(f"[boot] {RESTORE_SH} not present (T11 tar-cache job hasn't completed yet). "
              "GPU jobs that need the cosmos-predict1 env will fail until cache lands.")
        return
    print(f"[boot] running {RESTORE_SH} (expect ~5 min) ...")
    rc = subprocess.run(["bash", str(RESTORE_SH)], timeout=900).returncode
    if rc != 0:
        print(f"[boot] WARN: restore.sh exit={rc}. Marking restore corrupt.")
        flag = DRIVE_BASE / "worker" / "restore_failed.flag"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(_utc(), encoding="utf-8")


def install_acq():
    """pip install agent-colab-queue if missing."""
    try:
        import agent_colab_queue  # noqa: F401
        try:
            ver = agent_colab_queue.__version__  # type: ignore[attr-defined]
        except Exception:
            ver = "unknown"
        print(f"[boot] agent-colab-queue already installed: {ver}")
        return
    except ImportError:
        pass
    print("[boot] installing agent-colab-queue from GitHub ...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "git+https://github.com/QiPan-Ronnie/agent-colab-queue.git",
    ])
    import agent_colab_queue  # noqa: F401


def start_bootstrap_heartbeat(runtime_kind: str, runtime_name: str, active_jobs_ref: dict):
    """Daemon thread that writes a fallback heartbeat to Drive every 30 s.

    Separate from Worker._write_heartbeat (which writes every 5 s under
    worker/heartbeat.json). This fallback is so the main agent can detect
    "worker thread died but bootstrap process alive" by comparing the
    timestamps of the two heartbeat files.
    """
    BOOTSTRAP_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)

    def loop():
        while True:
            try:
                BOOTSTRAP_HEARTBEAT.write_text(json.dumps({
                    "ts_utc": _utc(),
                    "runtime": runtime_kind,
                    "runtime_name": runtime_name,
                    "current_job_ids": list(active_jobs_ref.keys()) if active_jobs_ref else [],
                    "bootstrap_pid": os.getpid(),
                }, indent=2), encoding="utf-8")
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name="bootstrap_heartbeat")
    t.start()


def start_worker(runtime_kind: str, runtime_name: str):
    """Start the RuntimeAwareWorker poll loop (foreground, blocks forever)."""
    sys.path.insert(0, str(REPO_DIR / "scripts"))  # so we can import runtime_filter
    from runtime_filter import RuntimeAwareWorker

    worker = RuntimeAwareWorker(
        repo_dir=str(REPO_DIR),
        drive_workspace_path=str(DRIVE_BASE),
        jobs_dir_in_repo="jobs",
        poll_interval_s=5.0,
        pull_interval_s=10.0,
        result_update_s=3.0,
        heartbeat_s=5.0,
        runtime_kind=runtime_kind,
        runtime_name=runtime_name,
    )

    start_bootstrap_heartbeat(runtime_kind, runtime_name, worker.active_jobs)
    worker.run(verbose=True)


def main():
    _lock_fd = _acquire_lock()  # noqa: F841 -- keep ref so lock lives
    print(f"[boot] {_utc()} starting Wave 0.5 worker bootstrap")

    runtime_kind, runtime_name = detect_runtime()
    print(f"[boot] runtime detected: {runtime_kind} ({runtime_name})")

    BOOT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    boot_log = BOOT_LOG_DIR / f"boot_{int(time.time())}_{runtime_kind}_{runtime_name}.json"
    boot_log.write_text(json.dumps({
        "ts_utc": _utc(),
        "runtime": runtime_kind,
        "runtime_name": runtime_name,
        "pid": os.getpid(),
    }, indent=2), encoding="utf-8")

    ensure_drive()
    ensure_repo()
    restore_gpu_env(runtime_kind)
    install_acq()

    print(f"[boot] {_utc()} starting worker with runtime filter (kind={runtime_kind})")
    start_worker(runtime_kind, runtime_name)


if __name__ == "__main__":
    main()
