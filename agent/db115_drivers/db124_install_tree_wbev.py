"""Install patched w2p_ego tree on the fresh A100 and launch the world-BEV render."""
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

W2P = r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama"
new_md5 = hashlib.md5(open(os.path.join(W2P, "scripts", "phase3", "db89_ghost_recovery.py"), "rb").read()).hexdigest()
a = dr2.get("a100")

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

OLD0 = "HOOD_SUPPORT_PX = 12.0  # px: a lower-nadir egoproj pixel whose nearest LiDAR return is farther than this (= no real surface there = ego hood/rig) gets filled; real near-cars return LiDAR (small dist) and stay protected (DB-106)."
NEW0 = OLD0 + "\n" + '''EGO_BLACK = False  # DB-123: scene-band ego-body removal. In GROUND_MODE="off" (band frames) the ego hood/body pixels (egoproj two-box gate & no-LiDAR-support, same discriminator as DB-114 HOOD_TO_MASK) are BLACKED OUT instead of kept — black is the band's honest "Cosmos will paint this" domain, and the mask twin (comp.sum>=12) follows automatically. Real near-cars overlapping the hood keep their LiDAR support and are protected. Default False (shipped band output unchanged); driver v6 sets True.
EGO_BLACK_DILATE = 9  # px: dilation margin around the ego-body mask (catches grazing z-error smear at the hood boundary that the 2-box gate misses, cf. EGO_IMG_MASK note).'''
OLD0b = '                    "low_coverage_warning": bool(len(flat_g) and anyg.mean() < 0.5)}'
NEW0b = OLD0b + "\n" + '''    if EGO_BLACK and GROUND_MODE == "off":   # DB-123: black out the ego body in band frames (see flag doc)
        _eb = egoproj.reshape(H, W) & (Zsupport > HOOD_SUPPORT_PX)
        _eb[:H // 2] = False
        if _eb.any():
            _eb = _cv.dilate(_eb.astype(np.uint8), np.ones((EGO_BLACK_DILATE, EGO_BLACK_DILATE), np.uint8)) > 0
            comp[_eb] = 0
        ground_stats["ego_black_px"] = int(_eb.sum())'''
E1o = """    proj = []
    if _bt:
        # DB115-PRO fix#5: same math as the CPU loop below, batched on GPU in float64
        # (float32 outputs bit-match the CPU path within dtype rounding).
        _dev = "cuda\""""
E1n = """    # DB-123 v2: per-camera image-domain ego-body mask (db118_egomask npz, quarter-res)
    # rejects hood/body SOURCE pixels in the main composite projection. The DB-114
    # geometric gate cannot work here: hood pixels get pasted at GROUND depth, whose
    # direction has real LiDAR support (v1 NEG — it blacked real road, kept the hood).
    _EIMC = None
    if EGO_IMG_MASK:
        _eimz_c = np.load(EGO_IMG_MASK)
        _EIMC = [(_eimz_c[c_] if c_ in _eimz_c.files else None) for c_ in ring_cams]
    _ego_rej = np.zeros(len(Xf), bool) if _EIMC is not None else None  # DB-123 C: pixels blacked BECAUSE of the ego mask
    proj = []
    if _bt:
        # DB115-PRO fix#5: same math as the CPU loop below, batched on GPU in float64
        # (float32 outputs bit-match the CPU path within dtype rounding).
        _dev = "cuda\""""
E2o = """            ok_t = (z_t > 0.1) & (px_t >= 1) & (px_t < ww - 1) & (py_t >= 1) & (py_t < hh - 1)
            pis_t = _th.zeros(Xf_t.shape[0], dtype=_th.bool, device=_dev)"""
E2n = """            ok_t = (z_t > 0.1) & (px_t >= 1) & (px_t < ww - 1) & (py_t >= 1) & (py_t < hh - 1)
            if _EIMC is not None and _EIMC[ci] is not None:   # DB-123: ego-body source pixels are no source at all
                _em = _EIMC[ci]
                _em_t = _th.as_tensor(_em.astype(np.uint8), device=_dev)
                _exi = _th.clamp((px_t / 4).long(), 0, _em.shape[1] - 1)
                _eyi = _th.clamp((py_t / 4).long(), 0, _em.shape[0] - 1)
                _okb_t = ok_t.clone()
                ok_t = ok_t & ~(_em_t[_eyi, _exi] > 0)
                _ego_rej |= (_okb_t & ~ok_t).cpu().numpy()
            pis_t = _th.zeros(Xf_t.shape[0], dtype=_th.bool, device=_dev)"""
E3o = """            ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
            # poisoned: projection lands inside this camera's MOVING-object mask (matched instances only)"""
E3n = """            ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
            if _EIMC is not None and _EIMC[ci] is not None:   # DB-123: ego-body source pixels are no source at all
                _em = _EIMC[ci]
                _exi = np.clip((px / 4).astype(np.int64), 0, _em.shape[1] - 1)
                _eyi = np.clip((py / 4).astype(np.int64), 0, _em.shape[0] - 1)
                _okb = ok.copy()
                ok &= ~_em[_eyi, _exi]
                _ego_rej |= (_okb & ~ok)
            # poisoned: projection lands inside this camera's MOVING-object mask (matched instances only)"""
E4o = """    save_rgb(REMOTE_OUT / f"{run_name}_segcomposite.png", comp)
    save_rgb(REMOTE_OUT / f"{run_name}_vismask.png", vismask)"""
E4n = """    save_rgb(REMOTE_OUT / f"{run_name}_segcomposite.png", comp)
    if _ego_rej is not None:   # DB-123 C: exact "blacked because of ego" zone for temporal-fill composition
        _ez = _ego_rej.reshape(H, W) & (comp.astype(np.int32).sum(2) < 12)
        _ez[:H // 2] = False
        save_rgb(REMOTE_OUT / f"{run_name}_egozone.png", np.dstack([(_ez.astype(np.uint8) * 255)] * 3))
    save_rgb(REMOTE_OUT / f"{run_name}_vismask.png", vismask)"""

all_edits = [(OLD0, NEW0), (OLD0b, NEW0b), (E1o, E1n), (E2o, E2n), (E3o, E3n), (E4o, E4n)]
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
assert new_md5 in t, "PATCH MISMATCH vs " + new_md5
print("TREE_READY")

JOB = (
    "import subprocess, time\n"
    "t0 = time.time()\n"
    "p = subprocess.run(['python', '/content/wbw.py', 'wb0', '125', 'cd22abca-9150-3279-87a4-cb00ba517372',\n"
    "                    'results/db115pro/db123/cd22abca_wbev'],\n"
    "                   capture_output=True, text=True, timeout=1800)\n"
    "print('WB3_RENDER rc=%d %.0fs' % (p.returncode, time.time() - t0), flush=True)\n"
    "if p.returncode != 0:\n"
    "    print('WB3_ERR', (p.stdout or '')[-400:], (p.stderr or '')[-200:], flush=True)\n"
    "print('WB3_DONE', flush=True)\n"
)
a.dr_launch("wbev3", JOB)
print("WBEV3_LAUNCHED")
