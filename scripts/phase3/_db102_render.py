"""DB-102 STEP 1: render the 3 audit anchors in BEV mode (new) and FILL mode (current)
for A/B, fetch the segcomposite PNGs to agent/_db102/{bev,fill}/.

  python _db102_render.py
"""
from __future__ import annotations
import base64, pathlib
import video_gen_av2 as vg
import db89_ghost_recovery as m
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

CASES = [   # priority order: highway first (cleanest, lum_std 2-3 -> best speckle test)
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway", 260),
    ("02a00399-3857-444e-8db3-a8f58489c394", "bmw", 47),
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd", 45),
]
MODES = ["bev", "fill"]
LOCAL = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/_db102")


def payload(uuid, tag, a, mode):
    vg.DATASET = f"datasets/db102_{mode}"
    py = vg.batch_py(uuid, tag, [a])
    assert 'GROUND_MODE = "fill"' in py, "marker missing"
    return py.replace('GROUND_MODE = "fill"', f'GROUND_MODE = "{mode}"')


def submit(c, uuid, tag, a, mode):
    b = base64.b64encode(payload(uuid, tag, a, mode).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<r>', 'exec'))\nPY")
    return c.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content/waymo2panorama",
                            "timeout_s": 1800}, timeout=180)["job_id"]


if __name__ == "__main__":
    c = ColabClient()
    order = [(mode, uuid, tag, a) for (uuid, tag, a) in CASES for mode in MODES]
    jobs = []
    for mode, uuid, tag, a in order:
        jid = submit(c, uuid, tag, a, mode)
        jobs.append((mode, tag, a, jid))
        print("submitted", mode, tag, a, jid, flush=True)
    for mode, tag, a, jid in jobs:
        res = m.poll_job(c, jid, 1800)
        print(mode, tag, a, res.get("state"), "exit", res.get("exit_code"), flush=True)
        (LOCAL / mode).mkdir(parents=True, exist_ok=True)
        raw = c.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db102_{mode}/{tag}_a{a:03d}_segcomposite.png", max_size_mb=60)
        if raw:
            (LOCAL / mode / f"{tag}_a{a:03d}_segcomposite.png").write_bytes(raw)
            print("GOT", mode, f"{tag}_a{a:03d}", len(raw), flush=True)
        else:
            print("MISS", mode, tag, a, flush=True)
    print("RENDER_DONE", flush=True)
