"""ROUTE-2: middle-only base stitch videos (Fable-5 three-fixes core, GROUND_MODE='off' ->
skip STAGE-4 ground outpaint, sky left black => BLACK top + BLACK bottom + perfect middle,
exactly the db89_board look). Same 4 scenes / 93-frame windows as the v1 ground videos.

Reuses video_gen_av2's WINDOWS / NWIN / FPS / assemble / poll, and injects GROUND_MODE='off'
into the remote payload. Output (Drive, ISOLATED): datasets/route2_middle_v1/.

  python _route2_middle.py --test       # 1-frame smoke (highway a225) to verify the base look
  python _route2_middle.py --submit [--reverse] [--only=highway]
  python _route2_middle.py --poll
  python _route2_middle.py --assemble
"""
from __future__ import annotations
import base64, json, sys
import video_gen_av2 as vg
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

vg.DATASET = "datasets/route2_middle_v1"   # isolated; never clobber av2_ground_video_v1


def _payload(uuid: str, tag: str, anchors: list[int]) -> str:
    py = vg.batch_py(uuid, tag, anchors)            # uses vg.DATASET (patched), resume-safe, lean saves
    assert 'GROUND_MODE = "fill"' in py, "GROUND_MODE marker missing in remote payload"
    return py.replace('GROUND_MODE = "fill"', 'GROUND_MODE = "off"')   # middle-only base stitch


def submit_scene(client, uuid, tag, start, anchors=None, timeout_s=18000, reverse=False):
    if anchors is None:
        anchors = list(range(start, start + vg.NWIN))
        if reverse:
            anchors = anchors[::-1]
    b = base64.b64encode(_payload(uuid, tag, anchors).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<r2>', 'exec'))\nPY")
    sub = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                                "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    return sub["job_id"]


if __name__ == "__main__":
    client = ColabClient()
    if "--test" in sys.argv:
        u, tag, start = [w for w in vg.WINDOWS if w[1] == "highway"][0]
        jid = submit_scene(client, u, tag, start, anchors=[start])
        print(f"TEST submitted {tag} a{start:03d} job {jid}")
    elif "--poll" in sys.argv:
        vg.poll(client)
    elif "--assemble" in sys.argv:
        vg.assemble(client)
    else:
        rev = "--reverse" in sys.argv
        only = set(a.split("=", 1)[1] for a in sys.argv if a.startswith("--only="))
        jobs = {}
        for u, tag, start in vg.WINDOWS:
            if only and tag not in only:
                continue
            jobs[tag] = submit_scene(client, u, tag, start, reverse=rev)
            print("submitted", tag, jobs[tag])
        print(json.dumps({"submitted": list(jobs)}))
