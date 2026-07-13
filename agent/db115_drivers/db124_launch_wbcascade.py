"""Launch the 31-frame shared-map worldbev render (Tier2 of the cascade) on A100."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import warnings
warnings.warn = lambda *a, **k: None
import sys, time
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
import video_gen_av2 as V
tag, anchors = "c", list(range(110, 141))
uuid, outdir = "cd22abca-9150-3279-87a4-cb00ba517372", "/content/wbc_out"
py = V.batch_py(uuid, tag, anchors)
def rep(p, old, new):
    assert p.count(old) >= 1, "MISS: " + old[:60]
    return p.replace(old, new)
py = rep(py, "datasets/av2_ground_video_v1", outdir)
py = rep(py, "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val", "/content/localav2/val")
py = rep(py, "BAND_TORCH = False", "BAND_TORCH = True")
py = rep(py, 'EGO_IMG_MASK = ""', 'EGO_IMG_MASK = "/content/egomask_cur.npz"')
py = rep(py, 'GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"')
py = rep(py, "WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (79, 172)")
py = rep(py, 'WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "/content/wb0_worldmap.png"')
py = rep(py, 'WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "1249.621676,-107.802527"')
print("WBC_PATCHED_8REPS", flush=True)
t0 = time.time()
exec(compile(py, "<w>", "exec"))
print("WBC_ALL_DONE %.0fs" % (time.time() - t0), flush=True)
'''
a.dr_launch("wbcasc", JOB)
print("WBCASC_LAUNCHED")
