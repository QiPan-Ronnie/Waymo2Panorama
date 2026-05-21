"""Wave 0.5 新-W — RuntimeAwareWorker subclass with labels.requires filter.

Subclasses agent_colab_queue.Worker. Overrides .run() by vendoring a copy of
the upstream loop with an extra ~11-line filter block inserted after the
active-jobs check. If a job spec has `labels.requires` ∈ {"gpu", "cpu"} and
the current Colab runtime doesn't match, the worker:

  1. Writes a transient result `{state: "skipped_wrong_runtime"}` to
     results/<job_id>.json — once per (job_id, runtime_kind) pair.
  2. LEAVES jobs/<job_id>.json in the queue so a worker with the correct
     runtime picks it up later (no git delete).

Backward-compat: jobs without `labels.requires` default to "any" — all
existing in-flight jobs (T11 install / T1 multi-log / T11 inference /
tar-cache) keep running unaffected.

If upstream agent_colab_queue.Worker.run() changes, re-sync this file. The
inserted filter block is marked between BEGIN/END RUNTIME_FILTER comments
for easy diff.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone

from agent_colab_queue.worker import Worker as _BaseWorker


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeAwareWorker(_BaseWorker):
    """Worker that honours `labels.requires` in job specs."""

    def __init__(self, *args, runtime_kind: str = "any", runtime_name: str = "unknown", **kwargs):
        super().__init__(*args, **kwargs)
        if runtime_kind not in ("gpu", "cpu", "any"):
            raise ValueError(f"runtime_kind must be gpu/cpu/any, got {runtime_kind!r}")
        self._runtime_kind = runtime_kind
        self._runtime_name = runtime_name
        self._already_skipped: set[str] = set()

    def _write_heartbeat(self) -> None:
        """Extend upstream heartbeat with runtime info."""
        # Mirror upstream's payload (id, ts, active_jobs) + add runtime fields.
        try:
            payload = {
                "updated_at": _utcnow_iso(),
                "runtime": self._runtime_kind,
                "runtime_name": self._runtime_name,
                "active_jobs": list(self.active_jobs.keys()),
            }
            self.heartbeat_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def run(self, verbose: bool = True) -> None:  # vendored copy of upstream + filter
        last_pull = 0.0
        last_heartbeat = 0.0
        last_result_refresh: dict[str, float] = {}

        if verbose:
            print(f"[acq-worker] start @ {_utcnow_iso()} (runtime={self._runtime_kind}/{self._runtime_name})")
            print(f"[acq-worker] repo_dir={self.repo_dir}")
            print(f"[acq-worker] drive_base={self.drive_base}")
            print(f"[acq-worker] poll={self.poll_interval_s}s pull={self.pull_interval_s}s")
            print(f"[acq-worker] stop via: !touch {self.stop_flag}")

        try:
            while not self.stop_flag.exists():
                now = time.time()

                if now - last_heartbeat >= self.heartbeat_s:
                    self._write_heartbeat()
                    last_heartbeat = now

                if now - last_pull >= self.pull_interval_s:
                    self._git_pull()
                    last_pull = now

                if self.jobs_dir.exists():
                    for job_file in sorted(self.jobs_dir.glob("*.json")):
                        try:
                            spec = json.loads(job_file.read_text(encoding="utf-8"))
                            job_id = spec.get("id")
                        except (OSError, json.JSONDecodeError):
                            continue
                        if not job_id or job_id in self.active_jobs:
                            continue

                        # =============== BEGIN RUNTIME_FILTER (new-W §2) ===============
                        labels = spec.get("labels") or {}
                        req = (labels.get("requires", "any") or "any").lower()
                        if req not in ("any", self._runtime_kind):
                            skip_key = f"{job_id}::{self._runtime_kind}"
                            if skip_key not in self._already_skipped:
                                if verbose:
                                    print(f"[acq-worker] skip {job_id}: needs {req}, runtime={self._runtime_kind}")
                                self._write_result(job_id, {
                                    "id": job_id,
                                    "state": "skipped_wrong_runtime",
                                    "current_runtime": self._runtime_kind,
                                    "current_runtime_name": self._runtime_name,
                                    "needed": req,
                                    "skipped_at": _utcnow_iso(),
                                    "cmd": spec.get("cmd"),
                                })
                                self._already_skipped.add(skip_key)
                            # Leave jobs/<id>.json in queue for correct-runtime worker.
                            continue
                        # =============== END RUNTIME_FILTER ===============

                        res_path = self.results_dir / f"{job_id}.json"
                        if res_path.exists():
                            try:
                                prior = json.loads(res_path.read_text(encoding="utf-8"))
                                # NOTE: do NOT treat skipped_wrong_runtime as terminal — we want
                                # this worker (matching runtime) to RE-CLAIM after another worker
                                # skipped it.
                                if prior.get("state") in ("done", "crashed", "timeout"):
                                    continue
                            except (OSError, json.JSONDecodeError):
                                pass

                        if verbose:
                            print(f"[acq-worker] claiming {job_id}")
                        self._write_result(job_id, {
                            "id": job_id,
                            "state": "claimed",
                            "claimed_at": _utcnow_iso(),
                            "cmd": spec.get("cmd"),
                        })
                        try:
                            self.active_jobs[job_id] = self._start_job(spec)
                            if verbose:
                                print(f"[acq-worker]   pid={self.active_jobs[job_id]['proc'].pid}")
                        except Exception as e:  # noqa: BLE001
                            if verbose:
                                print(f"[acq-worker]   start failed: {e}")
                            self._write_result(job_id, {
                                "id": job_id,
                                "state": "crashed",
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "finished_at": _utcnow_iso(),
                            })

                for job_id in list(self.active_jobs.keys()):
                    if now - last_result_refresh.get(job_id, 0) < self.result_update_s:
                        continue
                    active = self.active_jobs[job_id]
                    state = self._refresh_result(job_id, active)
                    last_result_refresh[job_id] = now
                    if state in ("done", "crashed", "timeout"):
                        if verbose:
                            print(f"[acq-worker] {job_id} -> {state} (exit={active['proc'].poll()})")
                        try:
                            active["log_fd"].close()
                        except OSError:
                            pass
                        del self.active_jobs[job_id]
                        last_result_refresh.pop(job_id, None)

                time.sleep(self.poll_interval_s)

            if verbose:
                print(f"[acq-worker] stop flag detected, exiting at {_utcnow_iso()}")
        except KeyboardInterrupt:
            if verbose:
                print(f"[acq-worker] KeyboardInterrupt at {_utcnow_iso()}")
