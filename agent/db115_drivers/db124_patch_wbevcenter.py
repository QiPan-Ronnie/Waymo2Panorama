"""Patch WORLDBEV_CENTER into the A100 w2p_ego tree (mirror of local edits) + compute a125 ta."""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import re
P = "/content/w2p_ego/scripts/phase3/db89_ghost_recovery.py"
src = open(P).read()
if "WORLDBEV_CENTER" in src:
    print("PATCH_ALREADY", flush=True)
else:
    o1 = 'Empty = build normally.'
    assert src.count(o1) == 1, "E1 count=%d" % src.count(o1)
    src = src.replace(o1, o1 + '\nWORLDBEV_CENTER = ""  # DB-123 cascade: "x,y" city metres; pins the map grid origin so a WORLDBEV_FILL map built at one anchor stays registered when sampled from neighbouring anchors. Empty = anchor-centred (unchanged).')
    o2 = """        # EMC capture-time poses per camera (same as the per-cap path).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])"""
    assert src.count(o2) == 1, "E2 count=%d" % src.count(o2)
    src = src.replace(o2, o2 + """
        if WORLDBEV_CENTER:
            _mcx, _mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))""")
    open(P, "w").write(src)
    print("PATCH_WRITTEN", flush=True)
chk = open(P).read()
assert chk.count("WORLDBEV_CENTER") == 3, "ASSERT count=%d" % chk.count("WORLDBEV_CENTER")
print("PATCH_ASSERT_OK 3 refs", flush=True)

# ---- compute a125 ta with the SAME loader + interp math as the kernel ----
import sys, glob
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
import numpy as np, pandas as pd
from pathlib import Path
from waymo2panorama.data_io.av2_loader import AV2RingLoader
logs = glob.glob("/content/localav2/val/*")
log_dir = Path([d for d in logs if "cd22abca" in d or True][0])
cands = [d for d in logs if Path(d).name.startswith("cd22abca")]
if cands:
    log_dir = Path(cands[0])
print("LOG_DIR", log_dir.name, flush=True)
loader = AV2RingLoader(log_dir)
all_ts = loader.anchor_timestamps_ns()
ts = all_ts[125]
p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
ti = p["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); tss = (ti - t0).astype(np.float64)
tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
keep = np.concatenate([[True], np.diff(tss) > 0]); tss, tx = tss[keep], tx[keep]
lo, hi = tss.min(), tss.max()
tc = float(np.clip(float(int(ts) - t0), lo, hi))
ta = np.array([np.interp(tc, tss, tx[:, i]) for i in range(3)])
print("A125_TA %.6f,%.6f" % (ta[0], ta[1]), flush=True)

# stage the map png locally (skip Drive I/O during render)
import shutil
shutil.copy("/content/drive/MyDrive/koi_waymo2pano_colab/results/db115pro/db123/cd22abca_wbev/wb0_a125_worldmap.png", "/content/wb0_worldmap.png")
import cv2
m = cv2.imread("/content/wb0_worldmap.png")
print("MAP_STAGED", m.shape, flush=True)
print("PREP_DONE", flush=True)
'''
enc = base64.b64encode(JOB.encode()).decode()
r = a._exec("python -c \"import base64;exec(base64.b64decode('%s').decode())\"" % enc, 300)
print((r.get("log_tail") or "").encode("ascii", "replace").decode("ascii"))
