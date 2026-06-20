"""DB-103: assemble highway_fixed.mp4 = the original middle-only highway sequence
(route2_middle_v1, anchors 225-317) with the seam-fixed car-arc frames (db103_tfix,
a302-316) spliced in. Runs the assemble ON COLAB (PNGs already on Drive) so non-fixed
frames are NOT re-compressed and we don't fetch 93 PNGs locally. Fetches the mp4.

  python _db103_splice.py    # run AFTER the car-arc render (bfkrfp386) completes
"""
from __future__ import annotations
import base64, pathlib
import db89_ghost_recovery as m
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

c = ColabClient()
LOCAL = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/deliverables/route2_middle_v1")

CODE = r'''
import pathlib, subprocess
import cv2, numpy as np
from PIL import Image
ORIG = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/datasets/route2_middle_v1")
FIX  = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103_tfix")
OUT  = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103_tfix/highway_fixed.mp4")
FPS = 12
frames, nfix = [], 0
for a in range(225, 318):
    f = FIX / f"highway_a{a:03d}_segcomposite.png"
    o = ORIG / f"highway_a{a:03d}_segcomposite.png"
    if f.exists(): frames.append(f); nfix += 1
    elif o.exists(): frames.append(o)
print("FRAMES", len(frames), "FIXED_SPLICED", nfix, flush=True)
h, w = np.array(Image.open(frames[0])).shape[:2]
raw = pathlib.Path("/content/hwy_fixed_raw.mp4")
vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
for fp in frames:
    vw.write(np.array(Image.open(fp).convert("RGB"))[:, :, ::-1])
vw.release()
subprocess.run(["ffmpeg","-y","-i",str(raw),"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(OUT)],
               capture_output=True, timeout=600)
print("ASSEMBLED", OUT, OUT.stat().st_size if OUT.exists() else "MISSING", flush=True)
'''

b = base64.b64encode(CODE.encode()).decode()
bash = "set +x\npython - <<'EOF'\nimport base64\nexec(compile(base64.b64decode('" + b + "').decode(),'<sp>','exec'))\nEOF"
sub = c.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": 900}, timeout=180)
res = m.poll_job(c, sub["job_id"], 900)
print("state", res.get("state"), "exit", res.get("exit_code"))
print((res.get("log_tail") or "")[-400:])
raw = c.read_file("/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103_tfix/highway_fixed.mp4", max_size_mb=200)
if raw:
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / "highway_seamfixed.mp4").write_bytes(raw)
    print("GOT highway_seamfixed.mp4", len(raw))
else:
    print("MISS mp4")
print("SPLICE_DONE")
