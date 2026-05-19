"""
scripts/cell_drive_queue_worker.py

The complete content of the Colab cell that runs the Drive queue worker.

Paste WORKER_CELL_CODE into a NEW cell at the END of your Colab notebook and run it
ONCE per Colab session. The cell will print a heartbeat-style log line every few
seconds and never return (until you touch the stop.flag on Drive).

After the worker is running, the agent submits jobs by writing
``jobs/<id>.json`` into the repo, committing, and pushing. The worker picks them up
within ~10 seconds. Results land at
``/content/drive/MyDrive/koi_waymo2pano_colab/results/<id>.json``.

To stop the worker:
    !touch /content/drive/MyDrive/koi_waymo2pano_colab/worker/stop.flag
"""

WORKER_CELL_CODE = r"""
# Cell W — Drive queue worker (W2P-004)
# Paste this into a new cell at the end of the notebook and run it once.
# Will loop forever (or until worker/stop.flag is touched). MCP is NOT used.

import sys, os, subprocess

REPO_DIR        = '/content/Waymo2Panorama'
WORKSPACE_DRIVE = '/content/drive/MyDrive/koi_waymo2pano_colab'

# Ensure repo is up to date (worker continues pulling internally too)
subprocess.run(['git', '-C', REPO_DIR, 'pull'], capture_output=True, text=True)

# Make our package importable
if f'{REPO_DIR}/code' not in sys.path:
    sys.path.insert(0, f'{REPO_DIR}/code')

from waymo2panorama.utils.drive_queue import DriveQueueWorker

worker = DriveQueueWorker(
    repo_dir=REPO_DIR,
    drive_base=WORKSPACE_DRIVE,
    poll_interval_s=5.0,
    pull_interval_s=10.0,
    result_update_s=3.0,
    heartbeat_s=5.0,
)
worker.run(verbose=True)
"""


if __name__ == "__main__":
    print(WORKER_CELL_CODE)
