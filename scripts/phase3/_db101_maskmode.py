"""DB-101 MIDDLE-ONLY render: GROUND_MODE="mask" -> skip the STAGE-4 nadir outpaint, keep the
determinable scene band (incl. directly-seen near-ground), mask the unseen nadir cap (neutral +
alpha). Renders 4 scenes for a generality + defect check. Output: datasets/db101_maskmode/.
Run from scripts/phase3:  python _db101_maskmode.py   /   python _db101_maskmode.py --poll
"""
from __future__ import annotations
import json, base64
import dataset_gen_av2 as dg
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

dg.DATASET = "datasets/db101_maskmode"

JOBS = [
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway", [50, 70]),
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd",   [45]),
    ("02a00399",                              "bmw",     [40]),
    ("0bae3b5e-417d-3b03-abaa-806b433233b8", "clean",   [50]),
]


def submit(client, uuid, tag, anchors, timeout_s=2400):
    py = dg.batch_py(uuid, tag, anchors)
    assert 'GROUND_MODE = "fill"' in py, "GROUND_MODE marker missing"
    py = py.replace('GROUND_MODE = "fill"', 'GROUND_MODE = "mask"')   # middle-only
    b = base64.b64encode(py.encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<db101m>', 'exec'))\nPY")
    r = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                              "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    return r["job_id"]


if __name__ == "__main__":
    import sys
    client = ColabClient()
    if "--poll" in sys.argv:
        for uuid, tag, _ in JOBS:
            raw = client.read_file(
                f"/content/drive/MyDrive/koi_waymo2pano_colab/{dg.DATASET}/manifest_{tag}.json", max_size_mb=8)
            if not raw:
                print(tag, "pending"); continue
            d = json.loads(raw.decode("utf-8")); cs = d.get("cases", [])
            print(tag, d.get("status"), "ok", sum(1 for c in cs if "error" not in c),
                  "errs", [str(c.get("error"))[:120] for c in cs if "error" in c][:2])
    else:
        out = {}
        for uuid, tag, anchors in JOBS:
            out[tag] = submit(client, uuid, tag, anchors)
            print("submitted", tag, out[tag])
        print(json.dumps({"submitted": out}))
