"""
scripts/cell_acq_worker.py

The Colab cell content to run the agent-colab-queue worker for the waymo2panorama
workspace. **Replaces** the older cell at scripts/cell_drive_queue_worker.py once
the agent-colab-queue package is published to GitHub.

To use:
    1. (One-time) the agent registers the workspace via mcp__acq__register_workspace
       — already done; lives in ~/.agent-colab-queue/workspaces.yaml on the
       agent's machine. On Colab, the worker registers from CLI args inline.
    2. Replace the old worker cell's content with CELL_CODE below.
    3. Stop the old worker first (touch worker/stop.flag on Drive) so they don't
       race on the same jobs/ directory.
    4. Run the new cell.

The new worker uses the same jobs/ + results/ layout, so any in-flight jobs from
the old worker will be transparently picked up.
"""

CELL_CODE = r'''
# Cell — agent-colab-queue worker for waymo2panorama (W2P-005 / acq v0.1.0)
# Paste once per Colab session. Loops forever; does NOT use colab-mcp.

import sys, os, subprocess

# ====== EDIT THIS LINE if you adapt to a different workspace ======
WORKSPACE_NAME = "waymo2panorama"
# ==================================================================

# Inline workspace config (so this cell works even without ~/.agent-colab-queue/workspaces.yaml on Colab)
INLINE_CONFIG = {
    "repo_url":              "https://github.com/QiPan-Ronnie/Waymo2Panorama.git",
    "repo_local":            "/content/Waymo2Panorama",
    "drive_workspace_path":  "/content/drive/MyDrive/koi_waymo2pano_colab",
    "jobs_dir_in_repo":      "jobs",
}

# 1. Install acq if missing (one-time per Colab kernel)
try:
    import agent_colab_queue
    print(f"agent-colab-queue already installed: {agent_colab_queue.__version__}")
except ImportError:
    print("Installing agent-colab-queue from GitHub...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "git+https://github.com/QiPan-Ronnie/agent-colab-queue.git",
    ])
    import agent_colab_queue  # noqa: F401
    print(f"installed: {agent_colab_queue.__version__}")

# 2. Ensure repo is cloned and up-to-date
if not os.path.isdir(f"{INLINE_CONFIG['repo_local']}/.git"):
    print(f"Cloning {INLINE_CONFIG['repo_url']}...")
    subprocess.check_call(["git", "clone", INLINE_CONFIG["repo_url"], INLINE_CONFIG["repo_local"]])
else:
    subprocess.run(["git", "-C", INLINE_CONFIG["repo_local"], "pull"], capture_output=True)

# 3. Ensure Drive workspace folder exists (parent of results/ and worker/)
os.makedirs(INLINE_CONFIG["drive_workspace_path"], exist_ok=True)

# 4. Stop the OLD W2P-004 worker if it's running (writes its stop.flag for clean exit).
#    Safe to call even if no old worker exists — flag is just a file.
old_stop_flag = f"{INLINE_CONFIG['drive_workspace_path']}/worker/stop.flag"
os.makedirs(os.path.dirname(old_stop_flag), exist_ok=True)
# Don't pre-touch the flag — it would also stop the new worker we're about to start.
# Instead, document: if migrating from the W2P-004 worker, manually interrupt that cell first.

# 5. Start the new worker
from agent_colab_queue import Worker
worker = Worker(
    repo_dir=INLINE_CONFIG["repo_local"],
    drive_workspace_path=INLINE_CONFIG["drive_workspace_path"],
    jobs_dir_in_repo=INLINE_CONFIG["jobs_dir_in_repo"],
    poll_interval_s=5.0,
    pull_interval_s=10.0,
    result_update_s=3.0,
    heartbeat_s=5.0,
)
worker.run(verbose=True)
'''


if __name__ == "__main__":
    print(CELL_CODE)
