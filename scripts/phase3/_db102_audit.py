"""DB-102 STEP 0: NO-RENDER metric-domain (BEV) audit. Inject GROUND_MODE='bevaudit'
into db89 STAGE-4 for 3 anchors, fetch the per-cell radial-stats npy
({x,y,rr,ncount,graze_max,az_spread,lum_std}) to decide if the determinable 3-7 m
annulus is recoverable before building the BEV renderer.

  python _db102_audit.py
"""
from __future__ import annotations
import base64, pathlib
import video_gen_av2 as vg
import db89_ghost_recovery as m
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

vg.DATASET = "datasets/db102_bev"
CASES = [
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway", 260),
    ("02a00399-3857-444e-8db3-a8f58489c394", "bmw", 47),
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd", 45),
]
LOCAL = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/_db102")


def payload(uuid, tag, a):
    py = vg.batch_py(uuid, tag, [a])
    assert 'GROUND_MODE = "fill"' in py, "GROUND_MODE marker missing"
    return py.replace('GROUND_MODE = "fill"', 'GROUND_MODE = "bevaudit"')


def submit(c, uuid, tag, a):
    b = base64.b64encode(payload(uuid, tag, a).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<a>', 'exec'))\nPY")
    return c.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content/waymo2panorama",
                            "timeout_s": 1800}, timeout=180)["job_id"]


if __name__ == "__main__":
    c = ColabClient()
    LOCAL.mkdir(parents=True, exist_ok=True)
    jobs = {}
    for uuid, tag, a in CASES:
        jobs[(tag, a)] = submit(c, uuid, tag, a)
        print("submitted", tag, a, jobs[(tag, a)], flush=True)
    for (tag, a), jid in jobs.items():
        res = m.poll_job(c, jid, 1800)
        print(tag, a, res.get("state"), "exit", res.get("exit_code"), flush=True)
        tail = (res.get("log_tail") or "")[-600:]
        print(tail.encode("ascii", "replace").decode(), flush=True)
        raw = c.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/{vg.DATASET}/{tag}_a{a:03d}_bevaudit.npy", max_size_mb=50)
        if raw:
            (LOCAL / f"{tag}_a{a:03d}_bevaudit.npy").write_bytes(raw)
            print("GOT", f"{tag}_a{a:03d}_bevaudit.npy", len(raw), flush=True)
        else:
            print("MISS npy", tag, a, flush=True)
    print("AUDIT_DONE", flush=True)
