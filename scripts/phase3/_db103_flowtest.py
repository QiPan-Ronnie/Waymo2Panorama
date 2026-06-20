"""DB-103 candidate-fix test: render highway a309 with SEAM_FLOWMORPH=True (dense-flow
view-morph on high-residual seams) vs the baseline, fetch + crop the near-car to see if
the front-shear is fixed. Run AFTER the scope sweep frees an L4 (avoid concurrent-render OOM).

  python _db103_flowtest.py
"""
from __future__ import annotations
import base64, pathlib, json
import video_gen_av2 as vg
import db89_ghost_recovery as m
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

c = ColabClient()
vg.DATASET = "datasets/db103_flow"
U = "2c652f9e-8db8-3572-aa49-fae1344a875b"
LOCAL = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/_db103")
a = 309
py = (vg.batch_py(U, "highway", [a])
      .replace('GROUND_MODE = "fill"', 'GROUND_MODE = "off"')
      .replace('SEAM_FLOWMORPH = False', 'SEAM_FLOWMORPH = True'))
assert 'SEAM_FLOWMORPH = True' in py
b = base64.b64encode(py.encode()).decode()
bash = ("set +x\npython - <<'EOF'\nimport base64\n"
        "exec(compile(base64.b64decode('" + b + "').decode(), '<s>', 'exec'))\nEOF")
jid = c.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content/waymo2panorama", "timeout_s": 1800}, timeout=180)["job_id"]
print("JOB", jid, flush=True)
res = m.poll_job(c, jid, 1800)
print("state", res.get("state"), "exit", res.get("exit_code"), flush=True)
raw = c.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103_flow/highway_a{a:03d}_segcomposite.png", max_size_mb=60)
if raw:
    (LOCAL / "highway_a309_flow_seg.png").write_bytes(raw)
    import cv2
    im = cv2.imread(str(LOCAL / "highway_a309_flow_seg.png")); H, W = im.shape[:2]
    cv2.imwrite(str(LOCAL / "a309_leftcar_flow.png"), im[int(H * 0.40):int(H * 0.74), int(W * 0.12):int(W * 0.40)])
    print("GOT seg + crop", len(raw), flush=True)
mraw = c.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/datasets/db103_flow/manifest_video_highway.json", max_size_mb=8)
if mraw:
    cs = json.loads(mraw).get("cases", [{}])[0]
    for vm in cs.get("view_morph", []):
        print("VM", vm["cam_pair"], "max_reg_px", vm["max_reg_px"], "seam_diff_med", vm["seam_diff_med"], flush=True)
print("FLOWTEST_DONE", flush=True)
