"""DB-103 SCOPE SWEEP: quantify how prevalent near-ego large-parallax seam breakage is.
Render a sampled set of anchors per scene (GROUND off = scene band), read the
view-morph max_reg_px per seam (the near-object-break detector). Split across two L4s.

  python _db103_sweep.py --side A    # highway+bmw  on active_url L4 (wood-showing)
  python _db103_sweep.py --side B    # crowd+clean  on recipes-framing L4 (env)
"""
from __future__ import annotations
import sys, os, base64, json, pathlib

SCENES = {
    "highway": "2c652f9e-8db8-3572-aa49-fae1344a875b",
    "bmw": "02a00399-3857-444e-8db3-a8f58489c394",
    "crowd": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "clean": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}
ANCH = {"highway": [240, 280, 316], "bmw": [15, 50, 85], "crowd": [15, 50, 85], "clean": [15, 50, 85]}
SIDE = {"A": ["highway", "bmw"], "B": ["crowd", "clean"]}
LOCAL = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/_db103")

side = sys.argv[sys.argv.index("--side") + 1]
if side == "B":
    os.environ["COLAB_URL"] = "https://recipes-framing-zero-moral.trycloudflare.com"
    os.environ["COLAB_TOKEN"] = "83fab4ef766e2233a2ebccf4a6337ee3"

import video_gen_av2 as vg
import db89_ghost_recovery as m
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

c = ColabClient()
vg.DATASET = "datasets/db103sweep"
LOCAL.mkdir(parents=True, exist_ok=True)
rows = []
for scene in SIDE[side]:
    uuid = SCENES[scene]
    for a in ANCH[scene]:
        py = vg.batch_py(uuid, scene, [a]).replace('GROUND_MODE = "fill"', 'GROUND_MODE = "off"')
        b = base64.b64encode(py.encode()).decode()
        bash = ("set +x\npython - <<'EOF'\nimport base64\n"
                "exec(compile(base64.b64decode('" + b + "').decode(), '<s>', 'exec'))\nEOF")
        jid = c.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content/waymo2panorama",
                               "timeout_s": 1800}, timeout=180)["job_id"]
        res = m.poll_job(c, jid, 1800)
        mraw = c.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103sweep/manifest_video_{scene}.json", max_size_mb=8)
        worst, wseam, nbad = 0.0, "", 0
        if mraw:
            cs = json.loads(mraw).get("cases", [])
            cc = [x for x in cs if x.get("case") == f"{scene}_a{a:03d}"]
            cc = cc[0] if cc else (cs[-1] if cs else {})
            for vm in cc.get("view_morph", []):
                if vm.get("max_reg_px", 0) > worst:
                    worst = vm["max_reg_px"]; wseam = "/".join(s.replace("ring_", "") for s in vm["cam_pair"])
                if vm.get("max_reg_px", 0) > 8: nbad += 1
        rows.append({"scene": scene, "a": a, "worst_max_reg_px": worst, "seam": wseam, "n_bad_seams": nbad, "state": res.get("state")})
        print(scene, a, "worst_max_reg_px", worst, wseam, "n_bad", nbad, flush=True)
(LOCAL / f"sweep_{side}.json").write_text(json.dumps(rows, indent=1))
print("SWEEP_" + side + "_DONE", flush=True)
