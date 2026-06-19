"""DB-101 validation render: a few highway + crowd anchors through the visibility-gated
db89 (TARGET-side occlusion), output {tag}_a{NNN}_segcomposite.png + _vismask.png to an
ISOLATED Drive dir so existing data is untouched. Ground-only (no sky). The local
(edited) db89 source is injected via m.remote_py() -> no git push needed.

Run (from scripts/phase3, Colab creds in ~/.waymo2panorama/runtime/active_url.json):
  python _db101_render.py            # submit
  python _db101_render.py --poll     # check manifests
"""
from __future__ import annotations
import json, base64
import dataset_gen_av2 as dg
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

dg.DATASET = "datasets/db101_visibility"   # isolated output dir on Drive

JOBS = [
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway", [50, 60, 70]),
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd",   [45]),
]


def submit(client, uuid, tag, anchors, timeout_s=2400):
    py = dg.batch_py(uuid, tag, anchors)          # uses dg.DATASET; injects local edited db89
    b = base64.b64encode(py.encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<db101>', 'exec'))\nPY")
    r = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                              "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    return r["job_id"]


if __name__ == "__main__":
    import sys
    client = ColabClient()
    if "--poll" in sys.argv:
        for uuid, tag, _ in JOBS:
            raw = client.read_file(
                f"/content/drive/MyDrive/koi_waymo2pano_colab/{dg.DATASET}/manifest_{tag}.json",
                max_size_mb=8)
            if not raw:
                print(tag, "pending"); continue
            d = json.loads(raw); cs = d.get("cases", [])
            print(tag, d.get("status"),
                  "ok=" + str(sum(1 for c in cs if "error" not in c)),
                  "err=" + str(sum(1 for c in cs if "error" in c)))
    else:
        out = {}
        for uuid, tag, anchors in JOBS:
            out[tag] = submit(client, uuid, tag, anchors)
            print("submitted", tag, out[tag])
        print(json.dumps({"submitted": out}))
