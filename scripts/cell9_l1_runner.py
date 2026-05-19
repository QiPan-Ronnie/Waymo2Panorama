"""
Cell 9 (Phase 1) — paste THIS file's contents into Colab notebook Cell 9 when ready to run L1.

Why a separate file: until W2P-001 (colab-mcp bug) is resolved, we can't reliably update the
notebook cell via MCP. The user copy-pastes this content into Cell 9 in the Colab notebook
once the L1 code is on `main` (git pull in Cell 3 will fetch it).
"""

CELL_9_CONTENT = r"""
# Cell 9 — Phase 1 L1 baseline (sphere projection + multi-band blend)
import os
import subprocess
import glob
from IPython.display import Image, display, Markdown

# 1. Pull latest code (in case main updated since session start)
r = subprocess.run(['git', '-C', REPO_DIR, 'pull'], capture_output=True, text=True)
print(r.stdout)

# 2. Output dir on Drive (so result survives kernel restart)
L1_OUT_DIR = f'{WORKSPACE_DRIVE}/outputs/l1'
os.makedirs(L1_OUT_DIR, exist_ok=True)

# 3. Run L1 baseline over the first 5 seconds of the spike log
cmd = [
    'python', f'{REPO_DIR}/scripts/run_l1_baseline.py',
    '--log-dir',      LOG_PATH,
    '--out-dir',      L1_OUT_DIR,
    '--duration-sec', '5',
    '--save-frames',
]
print('Running:', ' '.join(cmd))
print('=' * 72)
r = subprocess.run(cmd, cwd=REPO_DIR)
print('=' * 72)
print(f'exit code: {r.returncode}')

# 4. Display first frame inline
log_id = os.path.basename(LOG_PATH)
frame_dir = f'{L1_OUT_DIR}/{log_id}'
frames = sorted(glob.glob(f'{frame_dir}/frame_*.png'))
mp4_path = f'{frame_dir}/baseline.mp4'

if frames:
    display(Markdown(f'### L1 baseline first frame — `{os.path.basename(frames[0])}`'))
    display(Image(filename=frames[0]))
    print(f'\\nTotal frames: {len(frames)}')
    print(f'mp4: {mp4_path}  (size: {os.path.getsize(mp4_path) // 1024} KB)')
else:
    print('No frames produced. Check exit code and stderr above.')
"""

if __name__ == "__main__":
    print(CELL_9_CONTENT)
