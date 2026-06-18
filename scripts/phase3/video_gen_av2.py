"""DB-97 ground-fill temporal-consistency VIDEOS: for each of 4 scenes, render a
window of 93 CONSECUTIVE anchors through the full v8 stack (scene band + STAGE-4
ground fill, NO sky), then assemble each scene's frames into one mp4. 4 clips.

Reuses the proven db89_ghost_recovery remote stack via dataset_gen's batch_py
pattern. Ground fill is already inside run_case (STAGE 4); sky is a SEPARATE
script we deliberately do NOT run, so the saved segcomposite = scene band + ground
with the sky left black — exactly the requested output.

Output (Drive): datasets/av2_ground_video_v1/<tag>_a<NNN>_segcomposite.png  +  <tag>.mp4

Modes:
  python video_gen_av2.py --diag            # per-log frame count + displacement profile
  python video_gen_av2.py --submit          # submit the 4 render jobs (resume-safe)
  python video_gen_av2.py --poll            # progress (frames done per scene)
  python video_gen_av2.py --assemble        # build the 4 mp4s from existing frames
"""
from __future__ import annotations
import base64, json, sys
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient
import db89_ghost_recovery as m

DATASET = "datasets/av2_ground_video_v1"
NWIN = 93
FPS = 12

# (log_uuid, tag, start_anchor) — start frames chosen by --diag motion profile
# (filled after the displacement diagnostic; windows must have sustained ego motion)
WINDOWS = [
    ("02a00399-3857-444e-8db3-a8f58489c394", "bmw", 0),       # 28.7 m over the window
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd", 0),     # 42.9 m (most motion)
    ("0bae3b5e-417d-3b03-abaa-806b433233b8", "clean", 0),     # 24.7 m
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway", 225), # 34.1 m (skips stationary start)
]

OLD_CASES = '''CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd"),
         ("0bae3b5e-417d-3b03-abaa-806b433233b8:30:clean", "0bae3b5e_a030_clean"),
         ("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway")]'''


def batch_py(log_uuid: str, tag: str, anchors: list[int]) -> str:
    py = m.remote_py()
    cases = ", ".join(f'("{log_uuid}:{a}:{tag}", "{tag}_a{a:03d}")' for a in anchors)
    assert OLD_CASES in py
    py = py.replace(OLD_CASES, "CASES = [" + cases + "]")
    py = py.replace("results/db89_ghost_recovery", DATASET)
    py = py.replace("DB89_remote_result.json", f"manifest_video_{tag}.json")
    # lean: drop emc + board copies (we only want the segcomposite per frame)
    py = py.replace('save_rgb(REMOTE_OUT / f"{run_name}_emc.png", emc)', "pass")
    py = py.replace('board.save(REMOTE_OUT / f"{run_name}_db89_board.jpg", quality=90)', "pass")
    # resume-safe + per-anchor isolation: skip a frame already on disk; one bad
    # anchor must not kill the 93-frame batch
    py = py.replace(
        "reports = [run_case(cs, rn) for cs, rn in CASES]",
        "reports = []\n"
        "    for cs, rn in CASES:\n"
        "        if (REMOTE_OUT / (rn + '_segcomposite.png')).exists():\n"
        "            reports.append({'case': rn, 'skipped': True}); continue\n"
        "        try:\n"
        "            reports.append(run_case(cs, rn))\n"
        "        except Exception as e:\n"
        "            reports.append({'case': rn, 'error': type(e).__name__ + ': ' + str(e)[:200]})",
    )
    return py


def submit_scene(client: ColabClient, log_uuid: str, tag: str, start: int, timeout_s: int = 18000,
                 reverse: bool = False) -> str:
    anchors = list(range(start, start + NWIN))
    if reverse:            # 2nd runtime chews from the top down; skip-if-exists dedups the middle overlap
        anchors = anchors[::-1]
    b = base64.b64encode(batch_py(log_uuid, tag, anchors).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<vidgen>', 'exec'))\nPY")
    sub = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                                "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    return sub["job_id"]


# ---- displacement diagnostic (pick windows with sustained ego motion) ----
DIAG = r'''
import json, pathlib, sys
import numpy as np, pandas as pd
DATA = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")
from waymo2panorama.data_io.av2_loader import AV2RingLoader
LOGS = __LOGS__
for uuid, tag in LOGS:
    ld = AV2RingLoader(DATA / uuid); ts = ld.anchor_timestamps_ns(); F = len(ts)
    pf = pd.read_feather(DATA / uuid / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns")
    ti = pf["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); tss = (ti - t0).astype(float)
    tx = pf[["tx_m","ty_m","tz_m"]].to_numpy(float)
    P = np.stack([np.interp(np.clip((np.asarray(ts,np.int64)-t0).astype(float),tss.min(),tss.max()), tss, tx[:,i]) for i in range(3)],1)
    # best 93-window: maximize total path length, penalize any stationary 10-frame sub-gap
    seg = np.linalg.norm(np.diff(P,axis=0),axis=1)            # per-frame step
    best=(−1,0) if False else (-1.0,0)
    for s in range(0, max(1,F-93)):
        w = seg[s:s+92]; total = w.sum(); minrun = min(w[k:k+10].sum() for k in range(0,len(w)-10)) if len(w)>10 else w.sum()
        score = total if minrun > 1.0 else total*0.1          # punish stationary stretch
        if score>best[0]: best=(score,s)
    cum=[round(float(np.linalg.norm(P[min(s+k,F-1)]-P[best[1]])),1) for s in [best[1]] for k in (0,23,46,69,92)]
    print(tag, "F=%d"%F, "best_start=%d"%best[1], "win_total_m=%.1f"%float(seg[best[1]:best[1]+92].sum()), "cum@0/23/46/69/92=",cum)
print("DIAG_DONE")
'''


def run_diag(client: ColabClient):
    logs = [(u, t) for (u, t, _) in WINDOWS] + [("9f871fb4-3b8e-34b3-9161-ed961e71a6da", "downtown")]
    py = DIAG.replace("__LOGS__", json.dumps(logs)).replace("−1", "-1")
    b = base64.b64encode(py.encode()).decode()
    bash = "set +x\npython - <<'PY'\nimport base64\nexec(compile(base64.b64decode('" + b + "').decode(),'<diag>','exec'))\nPY"
    sub = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content/waymo2panorama", "timeout_s": 600}, timeout=180)
    res = m.poll_job(client, sub["job_id"], 600)
    print(res.get("state"), "exit", res.get("exit_code"))
    print((res.get("log_tail") or "")[-2500:].encode("ascii", "replace").decode())


def poll(client: ColabClient):
    for u, tag, start in WINDOWS:
        raw = client.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/{DATASET}/manifest_video_{tag}.json", max_size_mb=8)
        if raw:
            d = json.loads(raw); cases = d.get("cases", [])
            ok = sum(1 for c in cases if "error" not in c and not c.get("skipped"))
            sk = sum(1 for c in cases if c.get("skipped")); er = sum(1 for c in cases if "error" in c)
            print(f"{tag}: status={d.get('status')} rendered={ok} skipped={sk} err={er} / {NWIN}")
        else:
            print(f"{tag}: pending")


ASSEMBLE = r'''
import pathlib, subprocess, json
import cv2, numpy as np
from PIL import Image
D = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/__DATASET__")
WINS = __WINS__
FPS = __FPS__
rep = {}
for tag, start, n in WINS:
    frames = [D / f"{tag}_a{a:03d}_segcomposite.png" for a in range(start, start+n)]
    frames = [f for f in frames if f.exists()]
    if len(frames) < 2: rep[tag] = f"only {len(frames)} frames"; continue
    h, w = np.array(Image.open(frames[0])).shape[:2]
    raw = D / f"{tag}_raw.mp4"; mp4 = D / f"{tag}.mp4"
    vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    for f in frames:
        im = np.array(Image.open(f).convert("RGB"))[:, :, ::-1]
        vw.write(im)
    vw.release()
    # re-encode to H.264/yuv420p so it plays in any player/browser (cv2 mp4v won't)
    subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(mp4)], capture_output=True, timeout=600)
    raw.unlink(missing_ok=True)
    rep[tag] = {"frames": len(frames), "mp4": str(mp4), "size_mb": round(mp4.stat().st_size/1e6, 2)}
    print("assembled", tag, rep[tag])
(D / "_video_manifest.json").write_text(json.dumps(rep, indent=1))
print("ASSEMBLE_DONE")
'''


def assemble(client: ColabClient):
    wins = [[t, s, NWIN] for (_, t, s) in WINDOWS]
    py = (ASSEMBLE.replace("__DATASET__", DATASET).replace("__WINS__", json.dumps(wins)).replace("__FPS__", str(FPS)))
    b = base64.b64encode(py.encode()).decode()
    bash = "set +x\npython - <<'PY'\nimport base64\nexec(compile(base64.b64decode('" + b + "').decode(),'<asm>','exec'))\nPY"
    sub = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": 900}, timeout=180)
    res = m.poll_job(client, sub["job_id"], 900)
    print(res.get("state"), "exit", res.get("exit_code"))
    print((res.get("log_tail") or "")[-2000:].encode("ascii", "replace").decode())


if __name__ == "__main__":
    client = ColabClient()
    if "--diag" in sys.argv:
        run_diag(client)
    elif "--poll" in sys.argv:
        poll(client)
    elif "--assemble" in sys.argv:
        assemble(client)
    elif "--submit" in sys.argv:
        only = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")]
        rev = "--reverse" in sys.argv
        for u, tag, start in WINDOWS:
            if only and tag not in only:
                continue
            jid = submit_scene(client, u, tag, start, reverse=rev)
            print(json.dumps({"tag": tag, "start": start, "rev": rev, "job_id": jid}))
    else:
        print("use --diag | --submit [--only=tag] | --poll | --assemble")
