"""DB-125 setup on fresh A100: tree(6-edit + WORLDBEV_CENTER) + ProPainter + s5cmd + scene lists."""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import importlib.util as _ilu

W2P = r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama"
TARGET_MD5 = "866c9ab25e73ad2c8f67ca303a2a7ccd"  # local db89 v8 (6-edit + WORLDBEV_CENTER)
a = dr2.get("a100")

# ---- step 1: base tree from Drive relay zip ----
r = a._exec(
    'Z="/content/drive/MyDrive/koi_waymo2pano_colab/bundles/w2p_bundle8_relay.zip"\n'
    "rm -rf /content/waymo2panorama /content/w2p_ego\n"
    'unzip -oq "$Z" -d /content\n'
    "cp -r /content/waymo2panorama /content/w2p_ego\n"
    "md5sum /content/w2p_ego/scripts/phase3/db89_ghost_recovery.py\n"
    "echo TREE_OK", 600)
t = r.get("log_tail") or ""
assert "cebab759a30a1af56f88d694ea6bd182" in t, "BASE TREE BAD: " + t[-150:]
print("BASE_OK")

# ---- step 2: apply the 6 DB-123 edits (verbatim from db124_install_tree_wbev) then the 2 WORLDBEV_CENTER edits ----
spec = _ilu.spec_from_file_location("_it", os.path.join(W2P, "agent", "db115_drivers", "db124_install_tree_wbev.py"))
# we only need the edit constants; execute the module in a sandbox that fakes dr2 to stop it from running
edits_src = open(os.path.join(W2P, "agent", "db115_drivers", "db124_install_tree_wbev.py"), encoding="utf-8").read()
ns = {}
head = edits_src[edits_src.index("OLD0 = "):edits_src.index("all_edits = ")]
exec(compile(head, "<edits>", "exec"), ns)
all_edits = [(ns["OLD0"], ns["NEW0"]), (ns["OLD0b"], ns["NEW0b"]), (ns["E1o"], ns["E1n"]),
             (ns["E2o"], ns["E2n"]), (ns["E3o"], ns["E3n"]), (ns["E4o"], ns["E4n"])]
# WORLDBEV_CENTER 2 edits (mirror of local v8)
C1o = 'WORLDBEV_FILL = ""  # DB-109 coherence test: path to a FLUX-filled world-BEV png; if set, worldbev OVERRIDES the built map with it so every target frame samples the SAME generated map ("generate once + sample" = temporal coherence by construction). Empty = build normally.'
C1n = C1o + '\nWORLDBEV_CENTER = ""  # DB-123 cascade: "x,y" city metres; pins the map grid origin so a WORLDBEV_FILL map built at one anchor stays registered when sampled from neighbouring anchors. Empty = anchor-centred (unchanged).'
C2o = """        # EMC capture-time poses per camera (same as the per-cap path).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])"""
C2n = C2o + """
        if WORLDBEV_CENTER:
            _mcx, _mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))"""
all_edits += [(C1o, C1n), (C2o, C2n)]

lines = ["import hashlib, base64",
         "p = '/content/w2p_ego/scripts/phase3/db89_ghost_recovery.py'",
         "src = open(p, encoding='utf-8').read()"]
for o, n in all_edits:
    ob = base64.b64encode(o.encode()).decode()
    nb = base64.b64encode(n.encode()).decode()
    lines.append("o = base64.b64decode('%s').decode(); n = base64.b64decode('%s').decode()" % (ob, nb))
    lines.append("assert src.count(o) == 1, 'anchor:' + o[:40]")
    lines.append("src = src.replace(o, n)")
lines.append("open(p, 'wb').write(src.encode('utf-8'))")
lines.append("print('PATCHED_MD5', hashlib.md5(open(p, 'rb').read()).hexdigest())")
PATCH = "\n".join(lines)
r = a._exec("python3 - <<'PYEOF'\n%s\nPYEOF" % PATCH, 300)
t = r.get("log_tail") or ""
print(t[-120:])
assert TARGET_MD5 in t, "PATCH MISMATCH vs " + TARGET_MD5
print("TREE_READY_V8")

# ---- step 3: push egomask module + wbw worker ----
for local, remote in [
    (os.path.join(W2P, "agent", "db115_drivers", "db123_egomask_analytic.py"), "/content/db123_egomask.py"),
]:
    b = base64.b64encode(open(local, "rb").read()).decode()
    a._exec("python3 -c \"import base64; open('%s','wb').write(base64.b64decode('%s'))\"" % (remote, b), 120)
print("MODULES_PUSHED")

# ---- step 4 (background): ProPainter + s5cmd + scene lists ----
JOB = r'''
import subprocess, time, os
def run(cmd, **kw):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1200, **kw)
    print("RUN[%s] rc=%d %.0fs" % (cmd[:50], r.returncode, time.time() - t0), flush=True)
    if r.returncode != 0:
        print((r.stderr or "")[-300:], flush=True)
    return r
run("pip install -q s5cmd einops av")
run("git clone -q https://github.com/sczhou/ProPainter /content/ProPainter")
# weights pre-download (inference auto-downloads too; prefetch to save time)
run("mkdir -p /content/ProPainter/weights")
r = run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/ > /content/val_list.txt; wc -l /content/val_list.txt")
print(open('/content/val_list.txt').read()[:200], flush=True)
run("ls /content/drive/MyDrive/koi_waymo2pano_colab/batch_band/ > /content/used_band.txt 2>/dev/null; ls /content/drive/MyDrive/koi_waymo2pano_colab/results/ > /content/used_results.txt 2>/dev/null; wc -l /content/used_band.txt /content/used_results.txt")
print("SETUP_DONE", flush=True)
'''
a.dr_launch("db125setup", JOB)
print("SETUP_LAUNCHED")
