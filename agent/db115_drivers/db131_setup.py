# -*- coding: utf-8 -*-
"""DB-131 dual-machine setup: v11 kernel tree (v9 chain + SHARD/DUMP/LOAD) + tools on both G4s."""
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

W2P = r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama"
KP = os.path.join(W2P, "scripts", "phase3", "db89_ghost_recovery.py")
TARGET = hashlib.md5(open(KP, "rb").read()).hexdigest()
print("target md5", TARGET)

# ---- collect the full edit chain: 6 (DB-123) + 2 (CENTER) + 5 (CAP v9) + 4 (SHARD v11) ----
it = open(os.path.join(W2P, "agent", "db115_drivers", "db124_install_tree_wbev.py"), encoding="utf-8").read()
ns = {}
exec(compile(it[it.index("OLD0 = "):it.index("all_edits = ")], "<e1>", "exec"), ns)
edits = [(ns["OLD0"], ns["NEW0"]), (ns["OLD0b"], ns["NEW0b"]), (ns["E1o"], ns["E1n"]),
         (ns["E2o"], ns["E2n"]), (ns["E3o"], ns["E3n"]), (ns["E4o"], ns["E4n"])]
C1o = 'WORLDBEV_FILL = ""  # DB-109 coherence test: path to a FLUX-filled world-BEV png; if set, worldbev OVERRIDES the built map with it so every target frame samples the SAME generated map ("generate once + sample" = temporal coherence by construction). Empty = build normally.'
C1n = C1o + '\nWORLDBEV_CENTER = ""  # DB-123 cascade: "x,y" city metres; pins the map grid origin so a WORLDBEV_FILL map built at one anchor stays registered when sampled from neighbouring anchors. Empty = anchor-centred (unchanged).'
C2o = """        # EMC capture-time poses per camera (same as the per-cap path).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])"""
C2n = C2o + """
        if WORLDBEV_CENTER:
            _mcx, _mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))"""
edits += [(C1o, C1n), (C2o, C2n)]
src126 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db126_patch_ab.py"), encoding="utf-8").read()
ns2 = {}
exec(compile(src126[src126.index("EDITS = ["):src126.index("lines = [")], "<e2>", "exec"), ns2)
edits += ns2["EDITS"]
# v11 shard edits
S1o = 'CAP_REF_TMPL = ""  # DB-126: printf-style glob template for an external band segcomposite used as the cast-correction truth ring when CAP_ONLY leaves comp black (self-reference would disable the cast fix). Empty = comp ring (unchanged).'
S1n = S1o + '''
WORLDBEV_SHARD = ""  # DB-131: "i,k" — this build only processes source frames _wfis[i::k]; combined with WORLDBEV_DUMP, K parallel shard workers replace the single-process map build (its 4-15min was the production critical path). Empty = full build (unchanged).
WORLDBEV_DUMP = ""  # DB-131: npz path; after the source-selection+sampling loops, dump (chosen, score, col) raw slot state for the shard-merge. Empty = no dump (unchanged).
WORLDBEV_LOAD = ""  # DB-131: npz path; SKIP both build loops and load merged (chosen, score, col) instead — the native post-processing (gain/median/tier/Telea) then runs unchanged, so the merge path re-uses the tuned pipeline instead of re-implementing it. Empty = build normally (unchanged).'''
S2o = """        _wfis = sorted(sorted(set(_pickf), key=lambda i_: abs(i_ - _aidx))[:110])
        _NSW = 6"""
S2n = """        _wfis = sorted(sorted(set(_pickf), key=lambda i_: abs(i_ - _aidx))[:110])
        if WORLDBEV_SHARD:   # DB-131: this worker builds only its interleaved share of the source frames
            _sh_i, _sh_k = (int(_v) for _v in WORLDBEV_SHARD.split(","))
            _wfis = _wfis[_sh_i::_sh_k]
        _NSW = 6"""
S3o = "        for _fi in (_wfis if not WORLDBEV_FILL else []):   # P1: skip the expensive build when a filled map overrides it"
S3n = "        for _fi in (_wfis if not (WORLDBEV_FILL or WORLDBEV_LOAD) else []):   # P1: skip the expensive build when a filled map overrides it; DB-131: or when merged shard state is loaded below"
S4o = """        _wcache.clear()
        _wh = ~np.isnan(_wcol[:, :, 0])"""
S4n = """        _wcache.clear()
        if WORLDBEV_DUMP:   # DB-131 shard worker: dump raw slot state for the merge, before any post-processing
            np.savez_compressed(WORLDBEV_DUMP, chosen=_wchosen, score=_wscore, col=_wcol)
            print("WORLDBEV_DUMPED", WORLDBEV_DUMP, int((_wchosen[0] >= 0).sum()), flush=True)
        if WORLDBEV_LOAD:   # DB-131 merge consumer: adopt merged slot state; the tuned post-processing below runs unchanged
            _wz_npz = np.load(WORLDBEV_LOAD)
            _wchosen = _wz_npz["chosen"]
            _wscore = _wz_npz["score"]
            _wcol = _wz_npz["col"]
            print("WORLDBEV_LOADED", WORLDBEV_LOAD, flush=True)
        _wh = ~np.isnan(_wcol[:, :, 0])"""
edits += [(S1o, S1n), (S2o, S2n), (S3o, S3n), (S4o, S4n)]

lines = ["import hashlib, base64",
         "p = '/content/w2p_ego/scripts/phase3/db89_ghost_recovery.py'",
         "src = open(p, encoding='utf-8').read()"]
for o, n in edits:
    ob = base64.b64encode(o.encode()).decode()
    nb = base64.b64encode(n.encode()).decode()
    lines.append("o = base64.b64decode('%s').decode(); n = base64.b64decode('%s').decode()" % (ob, nb))
    lines.append("assert src.count(o) == 1, 'anchor:' + o[:50]")
    lines.append("src = src.replace(o, n)")
lines.append("open(p, 'wb').write(src.encode('utf-8'))")
lines.append("print('PATCHED_MD5', hashlib.md5(open(p, 'rb').read()).hexdigest())")
PATCH = "\n".join(lines)

WORKER = r'''import warnings, sys, json
warnings.warn = lambda *a, **k: None
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
import video_gen_av2 as V
tag = sys.argv[1]
anchors = [int(x) for x in sys.argv[2].split(",")]
uuid = sys.argv[3]
outdir = sys.argv[4]
extra = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else []
py = V.batch_py(uuid, tag, anchors)
def rep(p, old, new):
    assert p.count(old) >= 1, "MISS: " + old[:60]
    return p.replace(old, new)
py = rep(py, "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_ground_video_v1", outdir)
py = rep(py, "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val", "/content/localav2/val")
py = rep(py, "BAND_TORCH = False", "BAND_TORCH = True")
py = rep(py, 'EGO_IMG_MASK = ""', 'EGO_IMG_MASK = "/content/egomask_cur.npz"')
for o, n in extra:
    py = rep(py, o, n)
exec(compile(py, "<w>", "exec"))
'''

MERGE = r'''# DB-131 map-shard merge: K npz -> global top-6 slots -> merged npz for WORLDBEV_LOAD
import sys, glob
import numpy as np
pat, out = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(pat))
assert files, pat
CH, SC, CO = [], [], []
for f in files:
    z = np.load(f)
    CH.append(z["chosen"]); SC.append(z["score"]); CO.append(z["col"])
ch = np.concatenate(CH, 0)   # (6K, N)
sc = np.concatenate(SC, 0)
co = np.concatenate(CO, 0)   # (6K, N, 3)
order = np.argsort(sc, axis=0)[:6]          # (6, N) best-6 per cell
cols = np.arange(sc.shape[1])[None, :]
np.savez_compressed(out, chosen=np.take_along_axis(ch, order, 0),
                    score=np.take_along_axis(sc, order, 0),
                    col=np.take_along_axis(co, order[:, :, None], 0))
print("MERGED", out, len(files))
'''

for g in ("a100", "g2"):
    a = dr2.get(g)
    r = a._exec(
        'Z="/content/drive/MyDrive/koi_waymo2pano_colab/bundles/w2p_bundle8_relay.zip"\n'
        "rm -rf /content/waymo2panorama /content/w2p_ego\n"
        'unzip -oq "$Z" -d /content\n'
        "cp -r /content/waymo2panorama /content/w2p_ego\n"
        "md5sum /content/w2p_ego/scripts/phase3/db89_ghost_recovery.py\necho TREE_OK", 600)
    t = r.get("log_tail") or ""
    assert "cebab759a30a1af56f88d694ea6bd182" in t, g + " BASE BAD: " + t[-150:]
    r = a._exec("python3 - <<'PYEOF'\n%s\nPYEOF" % PATCH, 300)
    t = r.get("log_tail") or ""
    assert TARGET in t, g + " PATCH MISMATCH: " + t[-120:]
    for content, remote in [(WORKER, "/content/db125_worker.py"), (MERGE, "/content/db131_merge.py"),
                            (open(os.path.join(W2P, "agent", "db115_drivers", "db123_egomask_analytic.py"), encoding="utf-8").read(), "/content/db123_egomask.py")]:
        b = base64.b64encode(content.encode()).decode()
        a._exec("python3 -c \"import base64; open('%s','wb').write(base64.b64decode('%s'))\"" % (remote, b), 120)
    JOB = r'''
import subprocess, time
def run(cmd, tmo=1500):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=tmo)
    print("RUN[%s] rc=%d" % (cmd[:50], r.returncode), flush=True)
run("pip install -q s5cmd einops av ftfy sentencepiece protobuf 'diffusers==0.38.0' 'transformers==4.56.2' accelerate")
import torch
print("TORCH", torch.__version__, torch.cuda.is_available(), flush=True)
run("git clone -q https://github.com/sczhou/ProPainter /content/ProPainter && mkdir -p /content/ProPainter/weights")
for f in ["ProPainter.pth", "recurrent_flow_completion.pth", "raft-things.pth"]:
    run("wget -q https://github.com/sczhou/ProPainter/releases/download/v0.1.0/%s -O /content/ProPainter/weights/%s" % (f, f), 600)
print("TOOLS_DONE", flush=True)
'''
    a.dr_launch("db131tools", JOB)
    print(g, "TREE_V11_OK + tools launched")
print("SETUP_BOTH_DONE")
